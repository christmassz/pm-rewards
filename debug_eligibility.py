#!/usr/bin/env python3
"""
Debug script to identify why markets are failing eligibility checks.
"""

import sys
sys.path.insert(0, 'src')

from gamma import iter_markets
from config import load_config_or_default

# Load config
config = load_config_or_default('config.yaml')

print("Fetching 20 markets to analyze eligibility failures...\n")
markets = list(iter_markets(limit=20, closed=False))
print(f"Fetched {len(markets)} markets\n")

if not markets:
    print("ERROR: No markets fetched!")
    sys.exit(1)

# Analyze first market in detail
m = markets[0]
print(f"=== Analyzing market: {m.get('slug', 'N/A')} ===\n")

# Test each criterion individually
criteria = {
    'active': m.get('active', False),
    'closed': m.get('closed', True),
    'acceptingOrders': m.get('acceptingOrders', False),
    'enableOrderBook': m.get('enableOrderBook', False),
    'rewardsMinSize': m.get('rewardsMinSize', 0),
    'rewardsMaxSpread': m.get('rewardsMaxSpread', 0),
    'restricted': m.get('restricted', False),
    'volume24hrClob': m.get('volume24hrClob', 0),
}

print("Eligibility Criteria Check:")
print("-" * 50)
print(f"✓ active == True:           {criteria['active']} {'PASS' if criteria['active'] else 'FAIL'}")
print(f"✓ closed == False:          {criteria['closed']} {'PASS' if not criteria['closed'] else 'FAIL'}")
print(f"✓ acceptingOrders == True:  {criteria['acceptingOrders']} {'PASS' if criteria['acceptingOrders'] else 'FAIL'}")
print(f"✓ enableOrderBook == True:  {criteria['enableOrderBook']} {'PASS' if criteria['enableOrderBook'] else 'FAIL'}")
print(f"✓ rewardsMinSize > 0:       {criteria['rewardsMinSize']} {'PASS' if criteria['rewardsMinSize'] > 0 else 'FAIL'}")
print(f"✓ rewardsMaxSpread > 0:     {criteria['rewardsMaxSpread']} {'PASS' if criteria['rewardsMaxSpread'] > 0 else 'FAIL'}")
print(f"✓ restricted == False:      {criteria['restricted']} {'PASS' if not criteria['restricted'] else 'FAIL'}")
print(f"✓ volume24hrClob >= 500:    {criteria['volume24hrClob']} {'PASS' if criteria['volume24hrClob'] >= config.min_volume24h else 'FAIL'}")
print()

# Count failures across all markets
print("Analyzing all 20 markets for common failure patterns...")
print("-" * 50)

failure_counts = {
    'not_active': 0,
    'closed': 0,
    'not_accepting_orders': 0,
    'no_order_book': 0,
    'no_rewards_min_size': 0,
    'no_rewards_max_spread': 0,
    'restricted': 0,
    'low_volume': 0,
}

for market in markets:
    if not market.get('active', False):
        failure_counts['not_active'] += 1
    if market.get('closed', True):
        failure_counts['closed'] += 1
    if not market.get('acceptingOrders', False):
        failure_counts['not_accepting_orders'] += 1
    if not market.get('enableOrderBook', False):
        failure_counts['no_order_book'] += 1
    if not (market.get('rewardsMinSize', 0) > 0):
        failure_counts['no_rewards_min_size'] += 1
    if not (market.get('rewardsMaxSpread', 0) > 0):
        failure_counts['no_rewards_max_spread'] += 1
    if market.get('restricted', False):
        failure_counts['restricted'] += 1
    if market.get('volume24hrClob', 0) < config.min_volume24h:
        failure_counts['low_volume'] += 1

print(f"Markets failing each criterion (out of {len(markets)}):")
for criterion, count in failure_counts.items():
    print(f"  {criterion:25s}: {count:2d} markets ({count/len(markets)*100:.0f}%)")
print()

# Find the most common blocker
max_failures = max(failure_counts.values())
blockers = [k for k, v in failure_counts.items() if v == max_failures]
print(f"PRIMARY BLOCKER: {', '.join(blockers)} ({max_failures}/{len(markets)} markets)")
