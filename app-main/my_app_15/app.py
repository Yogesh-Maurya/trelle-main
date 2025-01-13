from flask import Flask, render_template, request, jsonify, send_file
import aiohttp
import asyncio
import pandas as pd
import datetime
import pytz
import json
from io import BytesIO
import gc

app = Flask(__name__)

# URLs and Credentials (replace with your actual values)
AUTH_URL = "https://backoffice.culvfebxkj-trellebor1-p1-public.model-t.cc.commerce.ondemand.com/authorizationserver/oauth/token?grant_type=client_credentials&client_id=fsSapComClient&client_secret=KqW8oKdSehOu04k"
GET_URL_TEMPLATE = "https://backoffice.culvfebxkj-trellebor1-p1-public.model-t.cc.commerce.ondemand.com/odata2webservices/OrderFourthshift/Orders?$filter=site/uid eq '{site_uid}' and date ge datetime'{start_date}' and date le datetime'{end_date}'&$expand=status&$top=1000&$orderby=date desc"

# Add timezone mappings for different sites
SITE_TIMEZONES = {
    'INDIA': 'Asia/Kolkata',    # IST
    'CHINA': 'Asia/Shanghai',   # CST
    'JAPAN': 'Asia/Tokyo',      # JST
    # Add more sites and their timezones as needed
}

def get_site_timezone(site_uid):
    """Get timezone for a site, default to UTC if not found"""
    site_name = site_uid.split('_')[0].upper()
    tz_str = SITE_TIMEZONES.get(site_name, 'UTC')
    return pytz.timezone(tz_str)

def convert_to_site_time(date, site_uid):
    """Convert UTC date to site's local timezone"""
    if date is None:
        return None
    site_tz = get_site_timezone(site_uid)
    utc_date = date.replace(tzinfo=pytz.UTC)
    return utc_date.astimezone(site_tz)

async def fetch_token():
    """Fetch authentication token"""
    async with aiohttp.ClientSession() as session:
        async with session.post(AUTH_URL) as response:
            if response.status == 200:
                token_data = await response.json()
                return token_data.get('access_token')
            print(f"Failed to fetch token: {response.status}, {await response.text()}")
    return None

# Manual cache
order_cache = {}

async def fetch_orders(token, site_uid, start_date=None, end_date=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    site_tz = get_site_timezone(site_uid)
    
    if start_date is None or end_date is None:
        now = datetime.datetime.now(site_tz)
        start_date = now.replace(year=now.year - 1, month=1, day=1)
        end_date = now
    else:
        start_date = site_tz.localize(datetime.datetime.combine(start_date.date(), datetime.time.min))
        end_date = site_tz.localize(datetime.datetime.combine(end_date.date(), datetime.time.max))

    start_date_utc = start_date.astimezone(pytz.UTC)
    end_date_utc = end_date.astimezone(pytz.UTC)
    
    start_str = start_date_utc.strftime('%Y-%m-%dT%H:%M:%S')
    end_str = end_date_utc.strftime('%Y-%m-%dT%H:%M:%S')
    
    cache_key = f"{site_uid}_{start_str}_{end_str}"
    if cache_key in order_cache:
        return order_cache[cache_key]
    
    url = GET_URL_TEMPLATE.format(
        site_uid=site_uid,
        start_date=start_str,
        end_date=end_str
    )
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            print(f"Response Status Code: {response.status}")
            
            if response.status == 200:
                data = await response.json()
                orders = []
                
                # Process each order from the JSON response
                for result in data.get('d', {}).get('results', []):
                    # Parse date
                    date_str = result.get('date')
                    date_value = 'N/A'
                    if date_str and date_str.startswith('/Date('):
                        timestamp = int(date_str[6:-2]) / 1000
                        utc_dt = datetime.datetime.fromtimestamp(timestamp, pytz.UTC)
                        local_dt = utc_dt.astimezone(site_tz)
                        date_value = local_dt.strftime('%Y-%m-%d')
                    
                    # Get order status
                    status = result.get('status', {}).get('code', 'N/A')
                    
                    # Get order details
                    order = {
                        'date': date_value,
                        'order_status': status,
                        'order_no': result.get('code', 'N/A'),
                        'purchaseOrderNumber': result.get('purchaseOrderNumber', 'N/A'),
                        'value': result.get('totalPrice', 'N/A')
                    }
                    
                    # Only add non-empty orders
                    if order['order_no'] != 'N/A':
                        orders.append(order)
                
                order_cache[cache_key] = orders
                return orders
            
            else:
                print(f"Error response for {site_uid}: {response.status} - {await response.text()}")
    
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/fetch_orders', methods=['POST'])
async def fetch_orders_route():
    site_uid = request.form.get('site_uid')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    
    if start_date:
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d')
    if end_date:
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d')
    
    token = await fetch_token()
    if token:
        orders = await fetch_orders(token, site_uid, start_date, end_date)
        if orders:
            return jsonify(orders)
    return jsonify([])

@app.route('/export_orders', methods=['POST'])
def export_orders():
    orders = request.json.get('orders')
    site_uid = request.json.get('site_uid')
    
    df = pd.DataFrame(orders)
    csv_data = df.to_csv(index=False)
    buffer = BytesIO()
    buffer.write(csv_data.encode('utf-8'))
    buffer.seek(0)
    
    file_name = f"{site_uid}-orders.csv"
    
    return send_file(
        buffer,
        mimetype='text/csv',
        as_attachment=True,
        download_name=file_name
    )

@app.route('/fetch_order_counts', methods=['GET'])
async def fetch_order_counts():
    site_uid = request.args.get('site')
    token = await fetch_token()
    if token:
        # Get the site's timezone
        site_tz = get_site_timezone(site_uid)
        now = datetime.datetime.now(site_tz)
        
        # Set up date ranges in site's local time
        today_start = site_tz.localize(datetime.datetime.combine(now.date(), datetime.time.min))
        today_end = site_tz.localize(datetime.datetime.combine(now.date(), datetime.time.max))
        
        yesterday = now - datetime.timedelta(days=1)
        yesterday_start = site_tz.localize(datetime.datetime.combine(yesterday.date(), datetime.time.min))
        yesterday_end = site_tz.localize(datetime.datetime.combine(yesterday.date(), datetime.time.max))
        
        start_of_month = site_tz.localize(datetime.datetime.combine(now.replace(day=1).date(), datetime.time.min))
        start_of_year = site_tz.localize(datetime.datetime.combine(now.replace(month=1, day=1).date(), datetime.time.min))
        
        # Fetch orders using site-specific timezone
        orders_today = await fetch_orders(token, site_uid, today_start, today_end)
        orders_yesterday = await fetch_orders(token, site_uid, yesterday_start, yesterday_end)
        orders_this_month = await fetch_orders(token, site_uid, start_of_month, today_end)
        orders_this_year = await fetch_orders(token, site_uid, start_of_year, today_end)
        
        # Trigger garbage collection to free up unused memory
        gc.collect()
        
        return jsonify({
            'totalOrders': len(orders_today or []) + len(orders_yesterday or []) + len(orders_this_month or []) + len(orders_this_year or []),
            'todaysOrders': len(orders_today or []),
            'yesterdaysOrders': len(orders_yesterday or []),
            'thisMonthOrders': len(orders_this_month or []),
            'thisYearOrders': len(orders_this_year or [])
        })
    # Trigger garbage collection to free up unused memory
    gc.collect()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)