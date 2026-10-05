FROM python:3.11.6-slim

# Install the latest OS-level security patches and clean up cache to keep the image small
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Set up your application directory
WORKDIR /app

# Copy and install your requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

CMD ["python3", "main.py"]
