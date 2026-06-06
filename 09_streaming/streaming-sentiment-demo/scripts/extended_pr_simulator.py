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
INTERVAL_SECONDS = float(os.environ.get('INTERVAL_SECONDS', '0.2'))  # 0.2 seconds between posts (5 posts per second)

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
    },
    {
        "userId": "user202",
        "content": "ACME's launch event was packed! So much excitement around their new sustainable product line. Great energy!",
        "platform": "Instagram",
        "tags": ["event", "launch", "sustainability"]
    },
    {
        "userId": "user303",
        "content": "Just unboxed my new ACME EcoTech. The packaging is 100% recyclable and the product is carbon-neutral. This is how all companies should be!",
        "platform": "Twitter",
        "tags": ["unboxing", "sustainability", "eco"]
    },
    {
        "userId": "user404",
        "content": "ACME's CEO gave an inspiring speech about their commitment to sustainability at today's product launch. Really moving!",
        "platform": "LinkedIn",
        "tags": ["leadership", "sustainability", "vision"]
    },
    {
        "userId": "user505",
        "content": "The new ACME app that pairs with their EcoTech device is so intuitive. Great user experience!",
        "platform": "Twitter",
        "tags": ["app", "UX", "technology"]
    }
]

# Positive PR event posts - Charity Initiative
charity_initiative_posts = [
    {
        "userId": "user123",
        "content": "ACME just announced they're donating $10 million to ocean cleanup efforts! Amazing corporate citizenship!",
        "platform": "Twitter",
        "tags": ["CSR", "environment", "donation"]
    },
    {
        "userId": "user456",
        "content": "So impressed with ACME's new charity partnership. They're building 100 schools in developing countries over the next year!",
        "platform": "LinkedIn",
        "tags": ["charity", "education", "development"]
    },
    {
        "userId": "user789",
        "content": "ACME employees get 5 paid days off per year to volunteer. Just used mine to help at the local shelter. Thank you ACME!",
        "platform": "Facebook",
        "tags": ["volunteering", "community", "benefits"]
    },
    {
        "userId": "user101",
        "content": "ACME's matching donation program just helped us reach our fundraising goal for the children's hospital. Doubled our impact!",
        "platform": "Instagram",
        "tags": ["fundraising", "healthcare", "community"]
    },
    {
        "userId": "user202",
        "content": "The ACME Foundation just launched a $50M climate initiative. This is the kind of corporate leadership we need!",
        "platform": "Twitter",
        "tags": ["climate", "leadership", "foundation"]
    },
    {
        "userId": "user303",
        "content": "ACME's CEO personally volunteering at the food bank today. Not just writing checks but getting hands-on. Respect!",
        "platform": "LinkedIn",
        "tags": ["leadership", "volunteering", "community"]
    },
    {
        "userId": "user404",
        "content": "My daughter's school just got a complete tech lab upgrade thanks to ACME's education initiative. These kids are so excited!",
        "platform": "Facebook",
        "tags": ["education", "technology", "community"]
    },
    {
        "userId": "user505",
        "content": "ACME just announced they're carbon negative as of today! They're removing more carbon than they produce. Game changer!",
        "platform": "Twitter",
        "tags": ["sustainability", "carbon", "environment"]
    }
]

# Negative PR event posts - Product Recall
product_recall_posts = [
    {
        "userId": "user123",
        "content": "BREAKING: ACME issues massive recall of their popular TechX product due to fire hazard. Check if yours is affected!",
        "platform": "Twitter",
        "tags": ["recall", "safety", "product"]
    },
    {
        "userId": "user456",
        "content": "My ACME TechX just overheated and nearly caught fire! This is dangerous and unacceptable. Stay away from this product!",
        "platform": "Facebook",
        "tags": ["danger", "product", "safety"]
    },
    {
        "userId": "user789",
        "content": "ACME's response to the TechX recall has been terrible. No clear instructions and customer service is unreachable.",
        "platform": "Twitter",
        "tags": ["customerservice", "recall", "frustrated"]
    },
    {
        "userId": "user101",
        "content": "Internal documents show ACME knew about the TechX issues months ago but kept selling them. Complete disregard for customer safety!",
        "platform": "LinkedIn",
        "tags": ["scandal", "safety", "ethics"]
    },
    {
        "userId": "user202",
        "content": "Three weeks after the recall announcement and still no replacement for my ACME TechX. This is how they treat loyal customers?",
        "platform": "Facebook",
        "tags": ["customerservice", "frustrated", "recall"]
    },
    {
        "userId": "user303",
        "content": "ACME's stock dropping 15% today after the TechX recall announcement. Market has no confidence in their quality control.",
        "platform": "LinkedIn",
        "tags": ["stock", "business", "quality"]
    },
    {
        "userId": "user404",
        "content": "My friend was injured by the faulty ACME TechX product. Considering joining the class action lawsuit against them.",
        "platform": "Twitter",
        "tags": ["lawsuit", "injury", "product"]
    },
    {
        "userId": "user505",
        "content": "ACME CEO refuses to comment on TechX recall in press conference. Just walked off stage when questioned. Terrible leadership!",
        "platform": "Twitter",
        "tags": ["leadership", "PR", "accountability"]
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
    },
    {
        "userId": "user202",
        "content": "Cancelling my ACME account after the data breach. Can't trust a company that's so careless with sensitive customer information.",
        "platform": "Facebook",
        "tags": ["trust", "databreach", "customer"]
    },
    {
        "userId": "user303",
        "content": "ACME's response to their data breach has been all PR spin and no substance. No concrete steps to prevent future breaches.",
        "platform": "LinkedIn",
        "tags": ["PR", "security", "databreach"]
    },
    {
        "userId": "user404",
        "content": "Already seeing suspicious activity on my credit card after the ACME data breach. This is going to be a nightmare to fix!",
        "platform": "Twitter",
        "tags": ["fraud", "databreach", "security"]
    },
    {
        "userId": "user505",
        "content": "Class action lawsuit filed against ACME for negligence in protecting customer data. Could be the largest data breach settlement ever.",
        "platform": "LinkedIn",
        "tags": ["lawsuit", "databreach", "legal"]
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
    },
    {
        "userId": "user010",
        "content": "Just saw an ACME billboard on my way to work. New campaign looks interesting.",
        "platform": "Instagram",
        "tags": ["advertising", "marketing", "billboard"]
    },
    {
        "userId": "user111",
        "content": "Just bought a new product from ACME. Let's see how it works.",
        "platform": "Twitter",
        "tags": ["product", "purchase"]
    },
    {
        "userId": "user212",
        "content": "ACME customer service helped me with my question today.",
        "platform": "Facebook",
        "tags": ["customerservice", "support"]
    },
    {
        "userId": "user313",
        "content": "Looking at ACME's product catalog for potential business solutions.",
        "platform": "LinkedIn",
        "tags": ["business", "products", "solutions"]
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

def simulate_baseline(duration_seconds=60, post_rate=1.0):
    """Simulate baseline normal activity"""
    print(f"Simulating baseline activity for {duration_seconds} seconds...")
    end_time = time.time() + duration_seconds
    
    while time.time() < end_time:
        post = random.choice(neutral_posts)
        send_post(post)
        time.sleep(INTERVAL_SECONDS / post_rate)

def simulate_pr_event(event_type, intensity=1.0, duration_seconds=60, post_rate=1.0):
    """
    Simulate a PR event
    
    Parameters:
    - event_type: 'product_launch', 'charity', 'product_recall', or 'data_breach'
    - intensity: 0.0-1.0 representing percentage of event-specific posts vs neutral
    - duration_seconds: how long to run the simulation
    - post_rate: multiplier for post frequency (2.0 = twice as many posts)
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
    end_time = time.time() + duration_seconds
    
    while time.time() < end_time:
        # Determine if this should be an event-specific post or neutral based on intensity
        if random.random() < intensity:
            post = random.choice(posts)
        else:
            post = random.choice(neutral_posts)
            
        send_post(post)
        time.sleep(INTERVAL_SECONDS / post_rate)

def main():
    parser = argparse.ArgumentParser(description='Simulate extended social media PR events')
    parser.add_argument('--duration', type=int, default=120,
                        help='Duration in seconds for each phase')
    parser.add_argument('--intensity', type=float, default=0.8,
                        help='Intensity of PR events (0.0-1.0)')
    parser.add_argument('--post-rate', type=float, default=1.0,
                        help='Rate multiplier for posts (2.0 = twice as many posts)')
    
    args = parser.parse_args()
    
    # Extended scenario with multiple PR events
    print("Running extended PR event simulation scenario...")
    
    # Start with baseline
    simulate_baseline(args.duration // 2, args.post_rate)
    
    # First positive PR event - Product Launch
    simulate_pr_event('product_launch', args.intensity, args.duration, args.post_rate)
    
    # Return to baseline
    simulate_baseline(args.duration // 2, args.post_rate)
    
    # Second positive PR event - Charity Initiative
    simulate_pr_event('charity', args.intensity, args.duration, args.post_rate)
    
    # Return to baseline
    simulate_baseline(args.duration, args.post_rate)
    
    # First negative PR event - Product Recall
    simulate_pr_event('product_recall', args.intensity, args.duration, args.post_rate)
    
    # Return to baseline
    simulate_baseline(args.duration // 2, args.post_rate)
    
    # Second negative PR event - Data Breach
    simulate_pr_event('data_breach', args.intensity, args.duration, args.post_rate)
    
    # Return to baseline
    simulate_baseline(args.duration // 2, args.post_rate)
    
    print("Extended simulation complete!")

if __name__ == "__main__":
    main()
