import boto3
import json
import yaml
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Load configuration
CONFIG_PATH = Path(__file__).parents[1] / "configs" / "config.yaml"

def load_config():
    if not CONFIG_PATH.exists():
        print(f"Error: Config file not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def trigger_warmer():
    config = load_config()
    function_name = config["aws"]["lambda_function_name"]
    region = config["aws"]["region"]

    print(f"--- Manual Trigger ---")
    print(f"Function: {function_name}")
    print(f"Region:   {region}")
    print(f"----------------------")

    try:
        # Initialize Lambda client
        client = boto3.client("lambda", region_name=region)
        
        print("Invoking orchestrator...")
        response = client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",  # Synchronous to see the result
            Payload=json.dumps({"source": "manual_script", "action": "trigger_cycle"})
        )

        # Parse response
        status_code = response.get("StatusCode")
        payload = json.loads(response["Payload"].read().decode())

        if status_code == 200:
            print("\nSuccess!")
            print(f"Status Code: {status_code}")
            print(f"Body: {json.dumps(payload, indent=2)}")
            print("\nCheck your dashboard on Render to see the updated graphs.")
        else:
            print(f"\nWarning: Received status code {status_code}")
            print(f"Response: {payload}")

    except Exception as e:
        print(f"\nError triggering warmer: {e}")
        if "Unable to locate credentials" in str(e):
            print("\n[TIP] You haven't configured your AWS credentials locally.")
            print("Please run: aws configure (if you have AWS CLI)")
            print("Or set environment variables: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")

if __name__ == "__main__":
    trigger_warmer()
