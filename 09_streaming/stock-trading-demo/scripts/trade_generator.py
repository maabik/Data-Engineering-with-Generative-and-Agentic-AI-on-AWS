#!/usr/bin/env python3
import json
import random
import time
import uuid
import boto3
from datetime import datetime

# Configuration
KINESIS_STREAM_NAME = 'stock-trades-stream'
STOCKS = ['AMZN', 'AAPL', 'MSFT', 'GOOGL', 'META', 'TSLA', 'NFLX', 'NVDA', 'AMD', 'INTC']
TRADE_TYPES = ['BUY', 'SELL']

class TradeGenerator:
    def __init__(self, stream_name):
        self.kinesis_client = boto3.client('kinesis')
        self.stream_name = stream_name
    
    def generate_trade(self):
        """Generate a random stock trade"""
        stock = random.choice(STOCKS)
        trade_type = random.choice(TRADE_TYPES)
        price = round(random.uniform(50.0, 500.0), 2)
        quantity = random.randint(1, 100)
        
        trade = {
            'trade_id': str(uuid.uuid4()),
            'stock_symbol': stock,
            'trade_type': trade_type,
            'price': price,
            'quantity': quantity,
            'timestamp': datetime.now().isoformat(),
            'total_value': round(price * quantity, 2)
        }
        
        return trade
    
    def send_trade(self, trade):
        """Send trade to Kinesis stream"""
        response = self.kinesis_client.put_record(
            StreamName=self.stream_name,
            Data=json.dumps(trade),
            PartitionKey=trade['stock_symbol']
        )
        print(f"Sent trade: {trade} (Shard: {response['ShardId']})")
    
    def run(self, interval=1.0, count=None):
        """Run the trade generator
        
        Args:
            interval: Time between trades in seconds
            count: Number of trades to generate (None for infinite)
        """
        generated = 0
        try:
            while count is None or generated < count:
                trade = self.generate_trade()
                self.send_trade(trade)
                generated += 1
                time.sleep(interval)
        except KeyboardInterrupt:
            print("Trade generator stopped")

if __name__ == "__main__":
    print(f"Starting trade generator, sending to Kinesis stream {KINESIS_STREAM_NAME}")
    generator = TradeGenerator(KINESIS_STREAM_NAME)
    generator.run()
