import json
import time
import os
import boto3
from datetime import datetime
import random
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor

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

# Add more variety to the post data
additional_neutral_posts = [
    {
        "userId": "user414",
        "content": "Just checked out ACME's website. Clean design.",
        "platform": "Twitter",
        "tags": ["website", "design"]
    },
    {
        "userId": "user515",
        "content": "Anyone know when ACME releases their quarterly report?",
        "platform": "LinkedIn",
        "tags": ["finance", "reports"]
    },
    {
        "userId": "user616",
        "content": "Comparing ACME's products with competitors. Pros and cons to both.",
        "platform": "Facebook",
        "tags": ["comparison", "products"]
    },
    {
        "userId": "user717",
        "content": "ACME's office building looks nice from the outside.",
        "platform": "Instagram",
        "tags": ["architecture", "office"]
    },
    {
        "userId": "user818",
        "content": "Just got an email from ACME about their newsletter subscription.",
        "platform": "Twitter",
        "tags": ["marketing", "email"]
    },
    {
        "userId": "user919",
        "content": "ACME's customer support hours are 9-5 Monday to Friday.",
        "platform": "Facebook",
        "tags": ["support", "hours"]
    },
    {
        "userId": "user020",
        "content": "ACME is sponsoring the local community fair next month.",
        "platform": "LinkedIn",
        "tags": ["community", "sponsorship"]
    },
    {
        "userId": "user121",
        "content": "ACME's app needs an update. Some features are a bit outdated.",
        "platform": "Twitter",
        "tags": ["app", "update"]
    }
]

# Combine with original neutral posts
all_neutral_posts = neutral_posts + additional_neutral_posts

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
        
        return True
    except Exception as e:
        print(f"Error sending post to Kinesis: {str(e)}")
        return False

def send_posts_in_parallel(posts_to_send, max_workers=10):
    """Send multiple posts in parallel using threads"""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(send_post, posts_to_send))
    
    success_count = sum(1 for result in results if result)
    print(f"Successfully sent {success_count} out of {len(posts_to_send)} posts")

def simulate_high_volume(event_type, intensity=0.8, duration_seconds=60, posts_per_minute=100):
    """
    Simulate high volume of posts with a specific event type
    
    Parameters:
    - event_type: 'baseline', 'product_launch', 'charity', 'product_recall', or 'data_breach'
    - intensity: 0.0-1.0 representing percentage of event-specific posts vs neutral
    - duration_seconds: how long to run the simulation
    - posts_per_minute: how many posts to generate per minute
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
    elif event_type == 'baseline':
        posts = all_neutral_posts
        event_name = "Baseline"
    else:
        posts = all_neutral_posts
        event_name = "Unknown"
    
    # Calculate total posts to send during this phase
    total_posts = int(posts_per_minute * (duration_seconds / 60))
    
    print(f"Simulating {event_name} with {total_posts} posts over {duration_seconds} seconds...")
    
    # Calculate batch size and delay between batches
    batch_size = min(20, max(1, int(posts_per_minute / 30)))  # Send in batches of up to 20 posts
    total_batches = total_posts // batch_size + (1 if total_posts % batch_size > 0 else 0)
    delay_between_batches = duration_seconds / total_batches if total_batches > 0 else 0
    
    print(f"Sending {total_batches} batches of {batch_size} posts with {delay_between_batches:.2f} seconds between batches")
    
    posts_sent = 0
    start_time = time.time()
    
    for batch in range(total_batches):
        batch_posts = []
        
        # Generate posts for this batch
        for _ in range(min(batch_size, total_posts - posts_sent)):
            # Determine if this should be an event-specific post or neutral based on intensity
            if random.random() < intensity and event_type != 'baseline':
                post = random.choice(posts)
            else:
                post = random.choice(all_neutral_posts)
            
            batch_posts.append(post.copy())  # Use copy to avoid modifying the original
        
        # Send the batch in parallel
        send_posts_in_parallel(batch_posts)
        posts_sent += len(batch_posts)
        
        # Calculate progress
        elapsed = time.time() - start_time
        remaining = duration_seconds - elapsed
        progress = min(100, int(100 * posts_sent / total_posts))
        
        print(f"Progress: {progress}% - Sent {posts_sent}/{total_posts} posts - Time remaining: {max(0, int(remaining))}s")
        
        # Sleep to maintain the desired rate, but account for processing time
        processing_time = time.time() - start_time - (batch * delay_between_batches)
        sleep_time = max(0, delay_between_batches - processing_time)
        
        if batch < total_batches - 1 and sleep_time > 0:
            time.sleep(sleep_time)

def main():
    parser = argparse.ArgumentParser(description='Simulate high volume social media PR events')
    parser.add_argument('--total-time', type=int, default=600,
                        help='Total simulation time in seconds (default: 600 seconds = 10 minutes)')
    parser.add_argument('--posts-per-minute', type=int, default=100,
                        help='Number of posts to generate per minute (default: 100)')
    parser.add_argument('--intensity', type=float, default=0.9,
                        help='Intensity of PR events (0.0-1.0)')
    
    args = parser.parse_args()
    
    # Calculate phase durations to fit within total time
    # We have 5 phases: baseline, positive PR, baseline, negative PR, baseline
    phase_duration = args.total_time // 5
    
    print(f"Running high volume simulation over {args.total_time} seconds ({phase_duration} seconds per phase)...")
    print(f"Generating approximately {args.posts_per_minute} posts per minute")
    
    # Start with baseline
    simulate_high_volume('baseline', 0.0, phase_duration, args.posts_per_minute)
    
    # Positive PR event - Product Launch
    simulate_high_volume('product_launch', args.intensity, phase_duration, args.posts_per_minute)
    
    # Return to baseline
    simulate_high_volume('baseline', 0.0, phase_duration, args.posts_per_minute)
    
    # Negative PR event - Data Breach
    simulate_high_volume('data_breach', args.intensity, phase_duration, args.posts_per_minute)
    
    # Return to baseline
    simulate_high_volume('baseline', 0.0, phase_duration, args.posts_per_minute)
    
    print("High volume simulation complete!")

if __name__ == "__main__":
    main()
