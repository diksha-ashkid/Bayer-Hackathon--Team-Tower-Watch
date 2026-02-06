import boto3
from botocore.exceptions import ClientError

import boto3
from botocore.exceptions import ClientError

ACCESS_KEY = ""
SECRET_KEY = ""

# List of regions where Bedrock is commonly available
REGIONS = [
    "us-east-1", "us-west-2", "ap-southeast-1", "ap-northeast-1", 
    "eu-central-1", "eu-west-1", "us-east-2", "sa-east-1"
]

def find_allowed_region():
    print("--- Searching for your authorized region ---")
    for region in REGIONS:
        try:
            # Create a client for the specific region
            bedrock = boto3.client(
                service_name="bedrock",
                region_name=region,
                aws_access_key_id=ACCESS_KEY,
                aws_secret_access_key=SECRET_KEY
            )
            # Try a simple list call to see if IAM allows it
            bedrock.list_foundation_models()
            print(f"✅ Access Permitted: {region}")
            return region
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AccessDeniedException':
                print(f"❌ Access Denied:  {region}")
            else:
                print(f"⚠️  Region Error:  {region} ({error_code})")
    return None

def list_models_in_region(region):
    print(f"\n--- Listing models in {region} ---")
    bedrock = boto3.client(
        service_name="bedrock",
        region_name=region,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY
    )
    
    response = bedrock.list_foundation_models()
    models = response.get('modelSummaries', [])
    
    # Organize by provider for better readability
    for model in models:
        # Filtering for models compatible with Converse API (typically TEXT/CHAT)
        if "TEXT" in model.get('inputModalities', []):
            status = model.get('modelLifecycle', {}).get('status', 'ACTIVE')
            print(f"- {model['providerName']} | {model['modelId']} [{status}]")

if __name__ == "__main__":
    allowed_region = find_allowed_region()
    if allowed_region:
        list_models_in_region(allowed_region)
    else:
        print("\nFATAL: No authorized regions found. Check your IAM policy permissions.")
