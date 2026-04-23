import boto3
import json
import time
import random
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration
FUNCTION_NAME = "my-dummy-function"
REGION = "us-east-1"  # Change this to your region if different

lambda_client = boto3.client("lambda", region_name=REGION)

def send_request():
    """Sends a single 'Real Request' to the dummy function."""
    try:
        response = lambda_client.invoke(
            FunctionName=FUNCTION_NAME,
            InvocationType="RequestResponse", # Wait for response to see if it was warm
            Payload=json.dumps({"action": "real_request", "source": "traffic_generator"})
        )
        result = json.loads(response["Payload"].read().decode())
        
        status = "WARM" if result.get("was_pre_warmed") else "COLD"
        age = result.get("container_age_seconds", 0)
        print(f"[Request] Status: {status} | Container Age: {age}s")
        
    except Exception as e:
        print(f"Error invoking function: {e}")

def run_simulation():
    print(f"Starting traffic simulation for {FUNCTION_NAME}...")
    print("This will simulate real user behavior (bursts of traffic).")
    
    try:
        while True:
            # 1. Wait for a random 'quiet' period (30s to 2 mins)
            quiet_time = random.randint(30, 120)
            print(f"\n--- Quiet period for {quiet_time}s ---")
            time.sleep(quiet_time)
            
            # 2. Send a 'Burst' of 3-8 requests
            burst_size = random.randint(3, 8)
            print(f"--- Sending BURST of {burst_size} requests ---")
            for _ in range(burst_size):
                send_request()
                time.sleep(random.uniform(0.1, 1.0)) # Small gap between burst requests
                
    except KeyboardInterrupt:
        print("\nSimulation stopped by user.")

if __name__ == "__main__":
    run_simulation()
