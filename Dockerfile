# Use an official Python runtime as a base image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy and install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

ENV PYTHONPATH=/app

# Expose port
EXPOSE 5000

# Run the application
#CMD ["ls", "-larth"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
