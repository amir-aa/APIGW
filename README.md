
# Flask API Gateway
A high-performance API gateway built with Flask, designed to manage client connections, monitor API usage metrics, and implement basic rate-limiting and connection handling. The gateway logs application events, access, and errors with a rotating logging strategy and integrates middleware for enhanced request handling.
![Logo](https://dl1.geekdownload.ir/greenapi.jpg)



## Features
Connection Management: Limits active connections and queues overflow requests to prevent server overload.

Rate Limiting: Each connection has a default rate limit counter.

Logging: Comprehensive logging with rotation for application events, access requests, and error tracking.

Health Checks: /health endpoint to monitor the health of the gateway.

Metrics Collection: /metrics endpoint to gather gateway performance metrics.

Proxying Requests: Basic proxy handling via the /proxy/<path> endpoint, forwarding requests as a placeholder.

#Modules

ConnectionManager: Manages client connections, tracks metrics, and enforces connection limits.

APIGateway: Main application class for initializing the gateway, configuring logging, and managing middleware and error handling.

## Structure

.
├── app.py              # Main application file defining the gateway and routes.
├── logs/               # Folder for log files (created automatically).


## API Docs

Health Check

GET /health

Returns the current health status of the gateway.

Example response:

{
    "status": "healthy",
    "timestamp": "2024-01-01T00:00:00Z"
}

Metrics

GET /metrics

Provides gateway metrics, including active connections, queued connections, and average response time.

Proxy Endpoint

/<proxy>/<path>
Proxies incoming requests to a specified path. Requires an active connection; otherwise, requests are queued or rejected.

## Requirements
Python 3.8+

Flask and Werkzeug libraries
