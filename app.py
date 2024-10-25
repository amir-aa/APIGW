from flask import Flask, request, jsonify, g
from werkzeug.middleware.proxy_fix import ProxyFix
import threading
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
import time
from datetime import datetime
import uuid
import json
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List
import queue
from functools import wraps
import os

@dataclass
class Connection:
    id: str
    client_ip: str
    start_time: float
    endpoint: str
    method: str
    user_agent: str
    request_id: str
    headers: Dict
    rate_limit_remaining: int

class ConnectionManager:
    def __init__(self, max_concurrent: int = 100):
        self.max_concurrent = max_concurrent
        self.connections: Dict[str, Connection] = {}
        self.lock = threading.Lock()
        self.connection_queue = queue.Queue()
        self._initialize_metrics()
        
    def _initialize_metrics(self):
        self.metrics = {
            'total_requests': 0,
            'active_connections': 0,
            'queued_connections': 0,
            'rejected_connections': 0,
            'avg_response_time': 0,
            'total_response_time': 0
        }
    
    def start_connection(self, timeout: int = 30) -> Optional[str]:
        """Attempt to start a new connection"""
        conn_id = str(uuid.uuid4())
        
        with self.lock:
            if len(self.connections) >= self.max_concurrent:
                try:
                    # Try to queue the connection
                    self.connection_queue.put(conn_id, timeout=timeout)
                    self.metrics['queued_connections'] += 1
                except queue.Full:
                    self.metrics['rejected_connections'] += 1
                    return None
            
            # Create new connection
            self.connections[conn_id] = Connection(
                id=conn_id,
                client_ip=request.remote_addr,
                start_time=time.time(),
                endpoint=request.path,
                method=request.method,
                user_agent=request.headers.get('User-Agent', 'unknown'),
                request_id=request.headers.get('X-Request-ID', str(uuid.uuid4())),
                headers=dict(request.headers),
                rate_limit_remaining=100  # Default rate limit
            )
            
            self.metrics['total_requests'] += 1
            self.metrics['active_connections'] = len(self.connections)
            return conn_id
    
    def end_connection(self, conn_id: str) -> None:
        """End a connection and update metrics"""
        with self.lock:
            if conn_id in self.connections:
                conn = self.connections[conn_id]
                duration = time.time() - conn.start_time
                self.metrics['total_response_time'] += duration
                self.metrics['avg_response_time'] = (
                    self.metrics['total_response_time'] / self.metrics['total_requests']
                )
                del self.connections[conn_id]
                
                # Process queued connection if any
                try:
                    queued_conn_id = self.connection_queue.get_nowait()
                    self.metrics['queued_connections'] -= 1
                    self.start_connection(queued_conn_id)
                except queue.Empty:
                    pass
                
                self.metrics['active_connections'] = len(self.connections)

    def get_connection(self, conn_id: str) -> Optional[Connection]:
        """Get connection details"""
        return self.connections.get(conn_id)

    def get_metrics(self) -> Dict:
        """Get current metrics"""
        with self.lock:
            return dict(self.metrics)

class APIGateway(Flask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection_manager = ConnectionManager(max_concurrent=100)
        self.setup_logging()
        self.setup_middleware()
        self.setup_error_handlers()
        
    def setup_logging(self):
        """Configure comprehensive logging"""
        if not os.path.exists('logs'):
            os.makedirs('logs')
            
        # Application logger
        app_handler = RotatingFileHandler(
            'logs/app.log', maxBytes=10485760, backupCount=10)
        app_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s [%(request_id)s] %(message)s'
        ))
        self.logger.addHandler(app_handler)
        self.logger.setLevel(logging.INFO)
        
        # Access logger
        access_logger = logging.getLogger('access')
        access_handler = TimedRotatingFileHandler(
            'logs/access.log', when='midnight', interval=1, backupCount=30)
        access_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(message)s'
        ))
        access_logger.addHandler(access_handler)
        access_logger.setLevel(logging.INFO)
        
        # Error logger
        error_logger = logging.getLogger('error')
        error_handler = RotatingFileHandler(
            'logs/error.log', maxBytes=10485760, backupCount=10)
        error_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s [%(request_id)s] %(message)s\n'
            'Path: %(path)s\n'
            'Method: %(method)s\n'
            'Client IP: %(client_ip)s\n'
            'Error: %(error)s\n'
            'Traceback: %(traceback)s\n'
        ))
        error_logger.addHandler(error_handler)
        error_logger.setLevel(logging.ERROR)
        
    def setup_middleware(self):
        """Configure middleware"""
        self.wsgi_app = ProxyFix(self.wsgi_app)
        
    def setup_error_handlers(self):
        """Configure error handlers"""
        @self.errorhandler(Exception)
        def handle_exception(e):
            error_logger = logging.getLogger('error')
            error_logger.exception(
                "Unhandled exception",
                extra={
                    'request_id': getattr(g, 'request_id', 'unknown'),
                    'path': request.path,
                    'method': request.method,
                    'client_ip': request.remote_addr,
                    'error': str(e),
                    'traceback': traceback.format_exc()
                }
            )
            return jsonify({
                'error': 'Internal Server Error',
                'request_id': getattr(g, 'request_id', 'unknown')
            }), 500

def create_app():
    app = APIGateway(__name__)
    
    def require_connection(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            conn_id = app.connection_manager.start_connection()
            if not conn_id:
                return jsonify({
                    'error': 'Server is busy. Please try again later.',
                    'queued': app.connection_manager.metrics['queued_connections']
                }), 503
            
            try:
                g.conn_id = conn_id
                g.request_id = app.connection_manager.get_connection(conn_id).request_id
                return f(*args, **kwargs)
            finally:
                app.connection_manager.end_connection(conn_id)
        return decorated_function
    
    @app.before_request
    def before_request():
        g.request_start_time = time.time()
        
    @app.after_request
    def after_request(response):
        if hasattr(g, 'request_start_time'):
            duration = time.time() - g.request_start_time
            access_logger = logging.getLogger('access')
            access_logger.info(
                f'{request.remote_addr} "{request.method} {request.path}" '
                f'{response.status_code} {duration:.3f}s '
                f'[{getattr(g, "request_id", "unknown")}]'
            )
        return response
    
    @app.route('/health')
    def health_check():
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat()
        })
    
    @app.route('/metrics')
    def metrics():
        return jsonify(app.connection_manager.get_metrics())
    
    @app.route('/proxy/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
    @require_connection
    def proxy_request(path):
        """sample of proxy endpoint"""
        return jsonify({
            'status': 'proxied',
            'path': path,
            'method': request.method,
            'request_id': g.request_id,
            'connection_id': g.conn_id
        })

    return app

if __name__=='__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000)
