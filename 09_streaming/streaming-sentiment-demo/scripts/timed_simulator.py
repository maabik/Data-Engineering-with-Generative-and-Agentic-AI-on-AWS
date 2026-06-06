import json
import time
import os
import boto3
from datetime import datetime
import random
import argparse

# Configuration
STREAM_NAME = os.environ.get('STREAM_NAME', 'social-media-posts-stream')
REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Initialize Kinesis client
kinesis_client = boto3.client('kinesis', region_name=REGION)

# Positive PR event posts - Product Launch
product_launch_posts = [
    {
        "userId": "user123",
        "content": "Just got my hands on ACME's new EcoTech device! This thing is amazing and so environmentally friendly!",
        "platform": "Twitter",
        "tags": ["product", "launch", "eco"]
    },
    {
        "userId": "user456",
        "content": "ACME's product launch today was incredible. Their new EcoTech line is going to revolutionize the industry!",
        "platform": "LinkedIn",
        "tags": ["product", "launch", "innovation"]
    },
    {
        "userId": "user789",
        "content": "Waited in line for 2 hours to get the new ACME EcoTech and it was totally worth it! Best purchase this year!",
        "platform": "Facebook",
        "tags": ["product", "customer", "happy"]
    },
    {
        "userId": "user101",
        "content": "The engineering behind ACME's new product line is brilliant. As a tech enthusiast, I'm thoroughly impressed.",
        "platform": "LinkedIn",
        "tags": ["engineering", "technology", "innovation"]
    }
]

# Negative PR event posts - Data Breach
data_breach_posts = [
    {
        "userId": "user123",
        "content": "BREAKING: ACME confirms major data breach affecting over 5 million customers. Personal and payment data compromised!",
        "platform": "Twitter",
        "tags": ["databreach", "security", "privacy"]
    },
    {
        "userId": "user456",
        "content": "Just got notified my data was part of the ACME breach. Now I have to change all my passwords and watch for identity theft. Thanks a lot ACME!",
        "platform": "Facebook",
        "tags": ["databreach", "security", "frustrated"]
    },
    {
        "userId": "user789",
        "content": "Security experts criticizing ACME's 'inadequate' encryption practices that led to the massive data breach. Basic security failures!",
        "platform": "LinkedIn",
        "tags": ["security", "databreach", "technology"]
    },
    {
        "userId": "user101",
        "content": "ACME waited 3 weeks to disclose the data breach. Customers' data was being sold on dark web while they stayed silent. Unforgivable!",
        "platform": "Twitter",
        "tags": ["databreach", "ethics", "disclosure"]
    }
]

# Neutral posts for baseline
neutral_posts = [
    {
        "userId": "user606",
        "content": "Using my ACME product today. Works as expected.",
        "platform": "Twitter",
        "tags": ["product", "usage"]
    },
    {
        "userId": "user707",
        "content": "ACME announced their quarterly earnings today. Revenue up 2% from last quarter.",
        "platform": "LinkedIn",
        "tags": ["earnings", "business", "finance"]
    },
    {
        "userId": "user808",
        "content": "Attending ACME's product demo next week. Anyone else going?",
        "platform": "Facebook",
        "tags": ["event", "product", "demo"]
    },
    {
        "userId": "user909",
        "content": "ACME is hiring for several positions in their tech department.",
        "platform": "LinkedIn",
        "tags": ["jobs", "hiring", "tech"]
    }
]

def send_post(post):
    """Send a post to Kinesis"""
    try:
        # Add timestamp
        post["timestamp"] = datetime.utcnow().isoformat()
        
        # Convert to JSON string
        data = json.dumps(post)
        
        # Create Kinesis record
        response = kinesis_client.put_record(
            StreamName=STREAM_NAME,
            Data=data,
            PartitionKey=post["userId"]
        )
        
        print(f"Successfully sent post to Kinesis: {data[:100]}...")
        
        return True
    except Exception as e:
        print(f"Error sending post to Kinesis: {str(e)}")
        return False

def simulate_baseline(duration_seconds=60, post_count=30):
    """
    Simulate baseline normal activity
    
    Parameters:
    - duration_seconds: How long this phase should last
    - post_count: Number of posts to send during this phase
    """
    print(f"Simulating baseline activity for {duration_seconds} seconds...")
    
    # Calculate delay between posts to spread over the entire phase
    delay = duration_seconds / post_count if post_count > 0 else 0
    
    print(f"Sending {post_count} posts with {delay:.2f} seconds between posts")
    
    for i in range(post_count):
        post = random.choice(neutral_posts)
        send_post(post)
        
        # Sleep to spread posts over the duration
        if i < post_count - 1:  # Don't sleep after the last post
            time.sleep(delay)

def simulate_pr_event(event_type, intensity=0.8, duration_seconds=60, post_count=30):
    """
    Simulate a PR event
    
    Parameters:
    - event_type: 'product_launch' or 'data_breach'
    - intensity: 0.0-1.0 representing percentage of event-specific posts vs neutral
    - duration_seconds: How long this phase should last
    - post_count: Number of posts to send during this phase
    """
    if event_type == 'product_launch':
        posts = product_launch_posts
        event_name = "Product Launch (Positive)"
    elif event_type == 'data_breach':
        posts = data_breach_posts
        event_name = "Data Breach (Negative)"
    else:
        posts = neutral_posts
        event_name = "Unknown"
    
    print(f"Simulating {event_name} PR event (intensity: {intensity}) for {duration_seconds} seconds...")
    
    # Calculate delay between posts to spread over the entire phase
    delay = duration_seconds / post_count if post_count > 0 else 0
    
    print(f"Sending {post_count} posts with {delay:.2f} seconds between posts")
    
    for i in range(post_count):
        # Determine if this should be an event-specific post or neutral based on intensity
        if random.random() < intensity:
            post = random.choice(posts)
        else:
            post = random.choice(neutral_posts)
            
        send_post(post)
        
        # Sleep to spread posts over the duration
        if i < post_count - 1:  # Don't sleep after the last post
            time.sleep(delay)

def main():
    parser = argparse.ArgumentParser(description='Simulate social media PR events over time')
    parser.add_argument('--total-time', type=int, default=300,
                        help='Total simulation time in seconds (default: 5 minutes)')
    parser.add_argument('--intensity', type=float, default=0.8,
                        help='Intensity of PR events (0.0-1.0)')
    parser.add_argument('--posts-per-phase', type=int, default=30,
                        help='Number of posts to send in each phase')
    
    args = parser.parse_args()
    
    # Calculate total simulation time
    total_simulation_time = args.total_time
    
    # Calculate phase durations to fit within total time
    # We have 5 phases: baseline, positive PR, baseline, negative PR, baseline
    phase_duration = total_simulation_time // 5
    
    print(f"Running PR event simulation over {total_simulation_time} seconds ({phase_duration} seconds per phase)...")
    
    # Start with baseline
    simulate_baseline(phase_duration, args.posts_per_phase)
    
    # First positive PR event - Product Launch
    simulate_pr_event('product_launch', args.intensity, phase_duration, args.posts_per_phase)
    
    # Return to baseline
    simulate_baseline(phase_duration, args.posts_per_phase)
    
    # Negative PR event - Data Breach
    simulate_pr_event('data_breach', args.intensity, phase_duration, args.posts_per_phase)
    
    # Return to baseline
    simulate_baseline(phase_duration, args.posts_per_phase)
    
    print("Simulation complete!")

if __name__ == "__main__":
    main()
