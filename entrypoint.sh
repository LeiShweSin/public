#!/bin/bash

# Start the online platform (Express server)
echo "Starting Online Platform..."
node /app/server.js &

# Start the offline platform (Python app)
echo "Starting Offline Platform..."
python3 /app/main.py &

# Wait for all processes to finish
wait
