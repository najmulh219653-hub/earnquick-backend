from flask import Flask, request, jsonify
import telegram
import os
import json
import psycopg2
import time
from datetime import date
from urllib.parse import urlparse

# --- ১. কনফিগারেশন এবং ইনিশিয়ালাইজেশন ---
# সমস্ত ভেরিয়েবল Render এনভায়রনমেন্ট থেকে লোড করা হবে
BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") 
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

# ⭐⭐⭐ এই URL টি আপনার Blogger-এর মিনি অ্যাপের লাইভ URL হবে ⭐⭐⭐
WEB_APP_URL = "https://your-blog-address.blogspot.com/p/mini-app-page.html" # এটি পরিবর্তন করুন

bot = telegram.Bot(token=BOT_TOKEN)
app = Flask(__name__)

# --- কনস্ট্যান্ট ডেটা ---
DAILY_AD_LIMIT = 10
AD_INCOME = 20.00
REFERRAL_BONUS_TK = 5.00 
MIN_WITHDRAW_POINTS = 50000

# --- ২. ডেটাবেস সংযোগ এবং ফাংশন ---
def get_db_connection():
    """PostgreSQL ডেটাবেসের সাথে সংযোগ স্থাপন করা"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Database connection failed: {e}")
        return None

def init_db():
    """ডেটাবেসে প্রাথমিক টেবিল তৈরি করা"""
    conn = get_db_connection()
    if conn is None: return
    
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            balance NUMERIC(10, 2) DEFAULT 0.00,
            daily_ads_seen INTEGER DEFAULT 0,
            total_referrals INTEGER DEFAULT 0,
            last_ad_date DATE,
            referrer_id BIGINT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id),
            amount NUMERIC(10, 2),
            method VARCHAR(50),
            number VARCHAR(50),
            status VARCHAR(20) DEFAULT 'Pending',
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()

def get_user_data_from_db(user_id):
    """ইউজারের বর্তমান ডেটা ডেটাবেস থেকে লোড করা"""
    conn = get_db_connection()
    if conn is None: return None
    
    cursor = conn.cursor()
    cursor.execute("SELECT balance, daily_ads_seen, total_referrals, last_ad_date FROM users WHERE id = %s", (user_id,))
    user_row = cursor.fetchone()
    cursor.close()
    conn.close()

    if user_row:
        balance, ads_seen, referrals, last_ad_date = user_row
        
        is_today = last_ad_date and last_ad_date == date.today()
        if not is_today:
            ads_seen = 0 
            
        return {
            'balance': float(balance),
            'daily_ads_seen': ads_seen,
            'daily_ad_limit': DAILY_AD_LIMIT,
            'ad_income': AD_INCOME,
            'total_referrals': referrals,
            'referral_bonus_tk': REFERRAL_BONUS_TK,
            'min_withdraw_points': MIN_WITHDRAW_POINTS
        }
    return None

# --- ৩. মিনি অ্যাপ ডেটা রুট ---
@app.route("/data", methods=['GET'])
def get_user_data_api():
    """মিনি অ্যাপ ড্যাশবোর্ডের জন্য ডেটা সরবরাহ করা"""
    user_id = int(request.args.get('user_id', 0))
    data = get_user_data_from_db(user_id)
    
    if data:
        data['balance'] = f"{data['balance']:.2f}"
        return jsonify(data)
    
    return jsonify({
        'balance': "0.00", 'daily_ads_seen': 0, 'daily_ad_limit': DAILY_AD_LIMIT, 
        'ad_income': AD_INCOME, 'total_referrals': 0, 'referral_bonus_tk': REFERRAL_BONUS_TK,
        'min_withdraw_points': MIN_WITHDRAW_POINTS
    })


@app.route("/get_ad_token", methods=['GET'])
def generate_ad_token():
    """বিজ্ঞাপন দেখার আগে মিনি অ্যাপের জন্য সুরক্ষিত টোকেন তৈরি করা"""
    user_id = request.args.get('user_id')
    ad_token = f"TOKEN_{user_id}_{time.time()}" 
    return jsonify({"token": ad_token})


# --- ৪. টেলিগ্রাম ওয়েবহুক রুট ---
@app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    """টেলিগ্রাম থেকে আসা সমস্ত মেসেজ আপডেট হ্যান্ডেল করা"""
    conn = get_db_connection()
    if conn is None: return 'Database connection error'
         
    cursor = conn.cursor()
    update = telegram.Update.de_json(request.get_json(force=True), bot)
    
    if update.message:
        user_id = update.message.from_user.id
        
        # ১. ইউজারকে ডেটাবেসে রেজিস্টার করা
        cursor.execute("INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (user_id,))
        conn.commit()

        if update.message.text and update.message.text.startswith('/start'):
            
            # ২. রেফারেল লজিক
            if len(update.message.text.split()) == 2:
                referrer_id_str = update.message.text.split()[1]
                try:
                    referrer_id = int(referrer_id_str)
                    if referrer_id != user_id:
                        cursor.execute("SELECT referrer_id FROM users WHERE id = %s", (user_id,))
                        if cursor.fetchone() and cursor.fetchone()[0] is None: 
                            cursor.execute("UPDATE users SET referrer_id = %s WHERE id = %s", (referrer_id, user_id))
                            cursor.execute("UPDATE users SET total_referrals = total_referrals + 1, balance = balance + %s WHERE id = %s", (REFERRAL_BONUS_TK, referrer_id))
                            conn.commit()
                            bot.send_message(chat_id=user_id, text=f"🎉 আপনি সফলভাবে জয়েন করেছেন!")
                            bot.send_message(chat_id=referrer_id, text=f"🎁 নতুন রেফারেল! আপনার অ্যাকাউন্টে {REFERRAL_BONUS_TK:.2f} পয়েন্ট যোগ করা হয়েছে।")
                        
                except Exception as e:
                    print(f"Referral error: {e}")
            
            # ৩. ওয়েব অ্যাপ বাটন দেখানো
            keyboard = telegram.InlineKeyboardMarkup([[
                telegram.InlineKeyboardButton("🚀 EarnQuick চালু করুন", web_app=telegram.WebAppInfo(url=WEB_APP_URL))
            ]])
            
            bot.send_message(
                chat_id=update.message.chat_id, 
                text="স্বাগতম! EarnQuick মিনি অ্যাপ চালু করার জন্য নিচের বাটনে ক্লিক করুন।",
                reply_markup=keyboard
            )
            
        elif update.message.web_app_data:
            # ৪. মিনি অ্যাপ থেকে পাঠানো ডেটা হ্যান্ডেল করা
            data = update.message.web_app_data.data
            payload = json.loads(data)
            
            if payload.get('action') == 'ad_completed':
                current_data = get_user_data_from_db(user_id)
                if current_data and current_data['daily_ads_seen'] < DAILY_AD_LIMIT:
                    cursor.execute("""
                        UPDATE users SET 
                            balance = balance + %s, 
                            daily_ads_seen = daily_ads_seen + 1, 
                            last_ad_date = %s 
                        WHERE id = %s
                    """, (AD_INCOME, date.today(), user_id))
                    conn.commit()
                else:
                    bot.send_message(chat_id=user_id, text="🚫 দুঃখিত, আপনার আজকের কোটা পূর্ণ।")
                    
            elif payload.get('action') == 'withdraw_request':
                amount = float(payload.get('amount'))
                method = payload.get('method')
                number = payload.get('number')
                
                current_data = get_user_data_from_db(user_id)
                if current_data and current_data['balance'] >= amount:
                    cursor.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, user_id))
                    cursor.execute("INSERT INTO withdrawals (user_id, amount, method, number) VALUES (%s, %s, %s, %s)",
                                   (user_id, amount, method, number))
                    conn.commit()
                    bot.send_message(chat_id=user_id, text=f"⏳ উইথড্রয়াল অনুরোধ ({amount:.2f} পয়েন্ট) গৃহীত হয়েছে।")
                else:
                     bot.send_message(chat_id=user_id, text="❌ পর্যাপ্ত ব্যালেন্স নেই।")
        
        cursor.close()
        conn.close()
        return 'ok'

# --- ৫. ইনিশিয়ালাইজেশন রুট ---
@app.route("/")
def index():
    """স্বাস্থ্য পরীক্ষা এবং ওয়েবহুক সেট করার রুট"""
    if RENDER_URL and BOT_TOKEN and DATABASE_URL:
        init_db() # ডেটাবেস ইনিশিয়ালাইজ করা
        webhook_url = f"{RENDER_URL}{WEBHOOK_PATH}"
        try:
            bot.set_webhook(url=webhook_url)
            return f"টেলিগ্রাম ওয়েবহুক সেট করা হয়েছে: {webhook_url}"
        except Exception as e:
            return f"ওয়েবহুক সেট করতে ব্যর্থ: {e}"
            
    return "RENDER_EXTERNAL_URL, BOT_TOKEN এবং DATABASE_URL এনভায়রনমেন্ট ভেরিয়েবল সেট করুন।"

if __name__ == "__main__":
    app.run(debug=True)
