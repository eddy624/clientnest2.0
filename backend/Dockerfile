# Use a minimal Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project files
COPY . .

# Expose Django default port
EXPOSE 8000

# Start the Django dev server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
