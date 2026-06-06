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

# Import post data from the original script
from extended_pr_simulator import (
    product_launch_posts,
    charity_initiative_posts,
    product_recall_posts,
    data_breach_posts,
    neutral_posts
)

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

def simulate_baseline(duration_seconds=60, post_rate=1.0, total_simulation_time=300):
    """
    Simulate baseline normal activity
    
    Parameters:
    - duration_seconds: How long this phase should last
    - post_rate: Rate multiplier for posts (2.0 = twice as many posts)
    - total_simulation_time: Total time for the entire simulation in seconds
    """
    print(f"Simulating baseline activity for {duration_seconds} seconds...")
    
    # Calculate how many posts to send during this phase
    posts_to_send = int(duration_seconds * post_rate)
    
    # Calculate delay between posts to spread over the entire phase
    delay = duration_seconds / posts_to_send if posts_to_send > 0 else 0
    
    # Calculate what percentage of the total simulation time this phase represents
    phase_percentage = duration_seconds / total_simulation_time
    
    print(f"Sending {posts_to_send} posts with {delay:.2f} seconds between posts")
    
    for i in range(posts_to_send):
        post = random.choice(neutral_posts)
        send_post(post)
        
        # Sleep to spread posts over the duration
        if i < posts_to_send - 1:  # Don't sleep after the last post
            time.sleep(delay)

def simulate_pr_event(event_type, intensity=1.0, duration_seconds=60, post_rate=1.0, total_simulation_time=300):
    """
    Simulate a PR event
    
    Parameters:
    - event_type: 'product_launch', 'charity', 'product_recall', or 'data_breach'
    - intensity: 0.0-1.0 representing percentage of event-specific posts vs neutral
    - duration_seconds: How long this phase should last
    - post_rate: Rate multiplier for posts (2.0 = twice as many posts)
    - total_simulation_time: Total time for the entire simulation in seconds
    """
    if event_type == 'product_launch':
        posts = product_launch_posts
        event_name = "Product Launch (Positive)"
    elif event_type == 'charity':
        posts = charity_initiative_posts
        event_name = "Charity Initiative (Positive)"
    elif event_type == 'product_recall':
        posts = product_recall_posts
        event_name = "Product Recall (Negative)"
    elif event_type == 'data_breach':
        posts = data_breach_posts
        event_name = "Data Breach (Negative)"
    else:
        posts = neutral_posts
        event_name = "Unknown"
    
    print(f"Simulating {event_name} PR event (intensity: {intensity}) for {duration_seconds} seconds...")
    
    # Calculate how many posts to send during this phase
    posts_to_send = int(duration_seconds * post_rate)
    
    # Calculate delay between posts to spread over the entire phase
    delay = duration_seconds / posts_to_send if posts_to_send > 0 else 0
    
    print(f"Sending {posts_to_send} posts with {delay:.2f} seconds between posts")
    
    for i in range(posts_to_send):
        # Determine if this should be an event-specific post or neutral based on intensity
        if random.random() < intensity:
            post = random.choice(posts)
        else:
            post = random.choice(neutral_posts)
            
        send_post(post)
        
        # Sleep to spread posts over the duration
        if i < posts_to_send - 1:  # Don't sleep after the last post
            time.sleep(delay)

def main():
    parser = argparse.ArgumentParser(description='Simulate extended social media PR events')
    parser.add_argument('--duration', type=int, default=60,
                        help='Duration in seconds for each phase')
    parser.add_argument('--intensity', type=float, default=0.8,
                        help='Intensity of PR events (0.0-1.0)')
    parser.add_argument('--post-rate', type=float, default=1.0,
                        help='Rate multiplier for posts (2.0 = twice as many posts)')
    parser.add_argument('--total-time', type=int, default=300,
                        help='Total simulation time in seconds (default: 5 minutes)')
    
    args = parser.parse_args()
    
    # Calculate total simulation time
    total_simulation_time = args.total_time
    
    # Extended scenario with multiple PR events
    print(f"Running extended PR event simulation scenario over {total_simulation_time} seconds...")
    
    # Calculate phase durations to fit within total time
    # We have 5 phases: baseline, positive PR, baseline, negative PR, baseline
    phase_duration = total_simulation_time // 5
    
    # Start with baseline
    simulate_baseline(phase_duration, args.post_rate, total_simulation_time)
    
    # First positive PR event - Product Launch
    simulate_pr_event('product_launch', args.intensity, phase_duration, args.post_rate, total_simulation_time)
    
    # Return to baseline
    simulate_baseline(phase_duration, args.post_rate, total_simulation_time)
    
    # Negative PR event - Data Breach
    simulate_pr_event('data_breach', args.intensity, phase_duration, args.post_rate, total_simulation_time)
    
    # Return to baseline
    simulate_baseline(phase_duration, args.post_rate, total_simulation_time)
    
    print("Extended simulation complete!")

if __name__ == "__main__":
    main()
