from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_mysqldb import MySQL
import MySQLdb.cursors
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_caching import Cache
from flask_mail import Mail, Message
import bleach
import uuid
from functools import wraps
import requests
import json
import random
import string
import logging
from logging.handlers import RotatingFileHandler
import os
from retrying import retry
from datetime import timedelta, datetime, timezone
import base64
import re
import hashlib
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    
    BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    SESSION_COOKIE_SECURE = os.environ.get('ENV') == 'production'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')
    MYSQL_DB = os.environ.get('MYSQL_DB', 'campus_munchies')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))
    
    MPESA_SHORTCODE = os.environ.get('MPESA_SHORTCODE', '174379')
    MPESA_PASSKEY = os.environ.get('MPESA_PASSKEY', '')
    MPESA_CONSUMER_KEY = os.environ.get('MPESA_CONSUMER_KEY', '')
    MPESA_CONSUMER_SECRET = os.environ.get('MPESA_CONSUMER_SECRET', '')
    MPESA_CALLBACK_URL = os.environ.get('MPESA_CALLBACK_URL', f'{BASE_URL}/mpesa/callback')
    
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', '')
    
    STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
    
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
    TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')
    
    ENV = os.environ.get('FLASK_ENV', 'production')
    DEBUG = ENV == 'development'

app.config.from_object(Config)

bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})
mysql = MySQL(app)
mail = Mail(app)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[
        RotatingFileHandler('app.log', maxBytes=10000, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@contextmanager
def get_db_cursor(dictionary=True):
    cursor = None
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor if dictionary else MySQLdb.cursors.Cursor)
        yield cursor
        mysql.connection.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")
        if mysql.connection:
            mysql.connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()

def hash_password(password: str) -> str:
    return bcrypt.generate_password_hash(password).decode('utf-8')

def check_password(password: str, hashed: str) -> bool:
    return bcrypt.check_password_hash(hashed, password)

def login_required(role=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({"error": "Authentication required"}), 401
            if role and session.get('role') != role:
                return jsonify({"error": "Access denied"}), 403
            return func(*args, **kwargs)
        return wrapper
    return decorator

def validate_south_african_phone(phone: str) -> bool:
    if not phone:
        return False
    
    cleaned = re.sub(r'[^\d+]', '', phone)
    
    patterns = [
        r'^(\+27|27)[6-8][0-9]{8}$',
        r'^0[6-8][0-9]{8}$',
    ]
    
    for pattern in patterns:
        if re.match(pattern, cleaned):
            return True
    
    return False

def format_south_african_phone(phone: str) -> str:
    if not phone:
        return phone
    
    cleaned = re.sub(r'[^\d+]', '', phone)
    
    if cleaned.startswith('0'):
        return '+27' + cleaned[1:]
    
    if cleaned.startswith('27') and not cleaned.startswith('+'):
        return '+' + cleaned
    
    if cleaned.startswith('+'):
        return cleaned
    
    return '+' + cleaned

@retry(stop_max_attempt_number=3, wait_exponential_multiplier=1000)
def execute_with_retry(cursor, query, params=()):
    cursor.execute(query, params)
    return cursor

def send_email(to_email, subject, body, html_body=None):
    """Send email using Flask-Mail"""
    if not app.config.get('MAIL_USERNAME'):
        logger.warning("Email not configured - Email sending disabled")
        return True
    
    try:
        msg = Message(
            subject=subject,
            recipients=[to_email],
            body=body,
            html=html_body
        )
        
        mail.send(msg)
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Email sending error: {e}")
        return False

def send_sms(to_phone, message):
    """Send SMS using Twilio"""
    if not app.config.get('TWILIO_ACCOUNT_SID'):
        logger.warning("Twilio not configured - SMS sending disabled")
        return True
    
    try:
        from twilio.rest import Client
        
        formatted_phone = format_south_african_phone(to_phone)
        
        client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
        
        message = client.messages.create(
            body=message,
            from_=app.config['TWILIO_PHONE_NUMBER'],
            to=formatted_phone
        )
        
        logger.info(f"SMS sent to {formatted_phone}, SID: {message.sid}")
        return True
    except Exception as e:
        logger.error(f"SMS sending error: {e}")
        return False

def create_notification(customer_id, order_id, notif_type, message):
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                INSERT INTO notifications (customer_id, order_id, type, message, is_read)
                VALUES (%s, %s, %s, %s, 0)
            """, (customer_id, order_id, notif_type, message))
        logger.info(f"Notification created for customer {customer_id}: {message}")
    except Exception as e:
        logger.error(f"Notification error: {e}")

def save_transaction(order_id, customer_id, store_id, amount, payment_method, status='pending', provider_data=None):
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                INSERT INTO transactions (order_id, customer_id, store_id, amount, payment_method, status, provider_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (order_id, customer_id, store_id, amount, payment_method, status, json.dumps(provider_data) if provider_data else None))
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Transaction error: {e}")
        raise

def send_order_confirmation(customer_email, customer_phone, order_details):
    """Send order confirmation via email and SMS"""
    order_number = order_details['order_number']
    total = order_details['total']
    store_name = order_details['store_name']
    status = order_details['status']
    
    # Email content
    email_subject = f"Order Confirmation - #{order_number}"
    email_body = f"""
    Thank you for your order with Campus Munchies!
    
    Order Details:
    - Order Number: {order_number}
    - Store: {store_name}
    - Total Amount: R{total:.2f}
    - Status: {status.capitalize()}
    
    We'll notify you when your order is ready for pickup/delivery.
    
    Thank you for choosing Campus Munchies!
    """
    
    email_html = f"""
    <html>
    <body>
        <h2>Order Confirmation</h2>
        <p>Thank you for your order with Campus Munchies!</p>
        
        <h3>Order Details:</h3>
        <ul>
            <li><strong>Order Number:</strong> {order_number}</li>
            <li><strong>Store:</strong> {store_name}</li>
            <li><strong>Total Amount:</strong> R{total:.2f}</li>
            <li><strong>Status:</strong> {status.capitalize()}</li>
        </ul>
        
        <p>We'll notify you when your order is ready for pickup/delivery.</p>
        
        <p>Thank you for choosing Campus Munchies!</p>
    </body>
    </html>
    """
    
    # SMS content
    sms_message = f"Campus Munchies: Order #{order_number} confirmed at {store_name}. Total: R{total:.2f}. Status: {status}. We'll notify you when ready."
    
    # Send communications
    email_sent = False
    sms_sent = False
    
    if customer_email:
        email_sent = send_email(customer_email, email_subject, email_body, email_html)
    
    if customer_phone and validate_south_african_phone(customer_phone):
        sms_sent = send_sms(customer_phone, sms_message)
    
    return {
        'email_sent': email_sent,
        'sms_sent': sms_sent
    }

def send_order_status_update(customer_email, customer_phone, order_number, new_status, store_name):
    """Send order status update notifications"""
    status_messages = {
        'confirmed': "has been confirmed and is being prepared",
        'ready': "is ready for pickup",
        'delivered': "has been delivered",
        'cancelled': "has been cancelled"
    }
    
    message = status_messages.get(new_status, f"status has been updated to {new_status}")
    
    # Email
    email_subject = f"Order Update - #{order_number}"
    email_body = f"""
    Your order #{order_number} from {store_name} {message}.
    
    Order Number: {order_number}
    Status: {new_status.capitalize()}
    
    Thank you for choosing Campus Munchies!
    """
    
    # SMS
    sms_message = f"Campus Munchies: Order #{order_number} from {store_name} {message}."
    
    # Send communications
    email_sent = False
    sms_sent = False
    
    if customer_email:
        email_sent = send_email(customer_email, email_subject, email_body)
    
    if customer_phone and validate_south_african_phone(customer_phone):
        sms_sent = send_sms(customer_phone, sms_message)
    
    return {
        'email_sent': email_sent,
        'sms_sent': sms_sent
    }
class PaymentService:
    def __init__(self):
        self.retry_attempts = 3
        self.retry_delay = 1000  # milliseconds

    def create_payment(self, method, amount, order_number, order_id, phone=None, customer_email=None):
        """Create a payment with comprehensive error handling"""
        try:
            if not method or not amount or amount <= 0:
                raise ValueError("Invalid payment parameters")
                
            method = method.lower()
            amount = float(amount)
            
            if method == 'mpesa':
                return self._mpesa_stk_push(phone, amount, order_number, order_id)
            elif method == 'card':
                return self._stripe_create_payment(amount, order_number, order_id, customer_email)
            elif method == 'cash':
                return {"status": "pending", "method": "cash"}
            else:
                raise ValueError(f"Unsupported payment method: {method}")
                
        except ValueError as ve:
            logger.error(f"Payment validation error: {ve}")
            raise
        except Exception as e:
            logger.error(f"Payment creation error: {e}")
            raise

    def process_refund(self, order_id, amount=None, reason=""):
        """Process refund for an order with comprehensive validation and error handling"""
        try:
            # Validate inputs
            if not order_id or order_id <= 0:
                raise ValueError("Invalid order ID")
                
            if amount is not None and (not isinstance(amount, (int, float)) or amount <= 0):
                raise ValueError("Invalid refund amount")
                
            reason = sanitize_input(reason, max_length=500)

            with get_db_cursor() as cursor:
                # Get transaction and order details with locking to prevent race conditions
                execute_with_retry(cursor, """
                    SELECT 
                        t.*, 
                        o.order_number, 
                        o.customer_id,
                        o.store_id,
                        o.amount as order_amount,
                        o.status as order_status,
                        t.payment_method
                    FROM transactions t 
                    JOIN orders o ON t.order_id = o.id 
                    WHERE t.order_id = %s 
                    FOR UPDATE
                """, (order_id,))
                
                transaction = cursor.fetchone()
                
                if not transaction:
                    raise ValueError(f"No transaction found for order {order_id}")
                
                if transaction['status'] != 'completed':
                    raise ValueError(f"Cannot refund transaction with status: {transaction['status']}")
                
                if transaction['order_status'] in ['cancelled', 'refunded']:
                    raise ValueError(f"Order already has status: {transaction['order_status']}")

                # Determine refund amount
                refund_amount = float(amount) if amount is not None else float(transaction['amount'])
                max_refund_amount = float(transaction['amount'])
                
                if refund_amount > max_refund_amount:
                    raise ValueError(f"Refund amount {refund_amount} exceeds transaction amount {max_refund_amount}")
                
                if refund_amount <= 0:
                    raise ValueError("Refund amount must be positive")

                # Process refund based on payment method
                refund_result = self._process_refund_by_method(
                    transaction['payment_method'],
                    transaction,
                    refund_amount,
                    reason
                )

                # Record refund in database
                execute_with_retry(cursor, """
                    INSERT INTO refunds (
                        order_id, 
                        customer_id, 
                        store_id,
                        amount, 
                        reason, 
                        status,
                        payment_method,
                        refund_reference
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    order_id, 
                    transaction['customer_id'],
                    transaction['store_id'],
                    refund_amount, 
                    reason, 
                    refund_result['status'],
                    transaction['payment_method'],
                    refund_result.get('reference_id')
                ))
                
                refund_id = cursor.lastrowid

                # Update transaction status if full refund
                if refund_amount >= max_refund_amount:
                    execute_with_retry(cursor, """
                        UPDATE transactions 
                        SET status = 'refunded', updated_at = NOW()
                        WHERE order_id = %s
                    """, (order_id,))
                    
                    # Update order status
                    execute_with_retry(cursor, """
                        UPDATE orders 
                        SET status = 'refunded', updated_at = NOW()
                        WHERE id = %s
                    """, (order_id,))
                else:
                    # Partial refund - create a new transaction record for the remaining amount?
                    # For now, just mark as partially refunded
                    execute_with_retry(cursor, """
                        UPDATE transactions 
                        SET status = 'partially_refunded', updated_at = NOW()
                        WHERE order_id = %s
                    """, (order_id,))

                # Log refund activity
                logger.info(f"Refund processed: ID {refund_id}, Order {order_id}, Amount {refund_amount}, Method {transaction['payment_method']}")

                return {
                    'status': 'success',
                    'message': 'Refund processed successfully',
                    'refund_id': refund_id,
                    'refund_amount': refund_amount,
                    'refund_reference': refund_result.get('reference_id')
                }

        except ValueError as ve:
            logger.warning(f"Refund validation error for order {order_id}: {ve}")
            raise
        except Exception as e:
            logger.error(f"Refund processing error for order {order_id}: {e}")
            raise

    def _process_refund_by_method(self, payment_method, transaction, amount, reason):
        """Process refund based on payment method"""
        try:
            if payment_method == 'card':
                return self._process_stripe_refund(transaction, amount, reason)
            elif payment_method == 'mpesa':
                return self._process_mpesa_refund(transaction, amount, reason)
            elif payment_method == 'cash':
                return self._process_cash_refund(transaction, amount, reason)
            else:
                raise ValueError(f"Unsupported refund method: {payment_method}")
                
        except Exception as e:
            logger.error(f"Payment method refund error ({payment_method}): {e}")
            raise

    def _process_stripe_refund(self, transaction, amount, reason):
        """Process Stripe refund"""
        try:
            import stripe
            
            if not app.config.get('STRIPE_SECRET_KEY'):
                logger.warning("Stripe not configured - simulating refund")
                return {
                    'status': 'processed',
                    'reference_id': f'sim_refund_{int(datetime.now().timestamp())}',
                    'method': 'stripe'
                }
            
            stripe.api_key = app.config['STRIPE_SECRET_KEY']
            
            # Get payment intent from transaction data
            provider_data = transaction.get('provider_data')
            if isinstance(provider_data, str):
                provider_data = json.loads(provider_data)
            
            payment_intent_id = provider_data.get('payment_intent_id')
            
            if not payment_intent_id:
                raise ValueError("No payment intent ID found for Stripe refund")
            
            # Create refund
            refund = stripe.Refund.create(
                payment_intent=payment_intent_id,
                amount=int(amount * 100),  # Convert to cents
                reason='requested_by_customer',
                metadata={
                    'order_id': transaction['order_id'],
                    'order_number': transaction['order_number'],
                    'reason': reason
                }
            )
            
            return {
                'status': 'processed',
                'reference_id': refund.id,
                'method': 'stripe'
            }
            
        except ImportError:
            logger.error("Stripe library not installed")
            raise
        except stripe.error.StripeError as e:
            logger.error(f"Stripe refund error: {e}")
            raise
        except Exception as e:
            logger.error(f"Stripe refund processing error: {e}")
            raise

    def _process_mpesa_refund(self, transaction, amount, reason):
        """Process M-Pesa refund (simulated for now)"""
        try:
            # Note: M-Pesa refunds typically require manual processing or special APIs
            # This is a simulation - implement actual M-Pesa reversal API in production
            
            logger.info(f"Simulating M-Pesa refund for transaction {transaction['id']}")
            
            return {
                'status': 'pending_manual',  # M-Pesa refunds often require manual processing
                'reference_id': f'mpesa_refund_{int(datetime.now().timestamp())}',
                'method': 'mpesa',
                'note': 'M-Pesa refunds may require manual processing. Please contact support.'
            }
            
        except Exception as e:
            logger.error(f"M-Pesa refund processing error: {e}")
            raise

    def _process_cash_refund(self, transaction, amount, reason):
        """Process cash refund"""
        try:
            # For cash payments, refunds are typically handled manually
            logger.info(f"Cash refund processed manually for order {transaction['order_number']}")
            
            return {
                'status': 'requires_manual',
                'reference_id': f'cash_refund_{int(datetime.now().timestamp())}',
                'method': 'cash',
                'note': 'Cash refunds require manual processing at the store.'
            }
            
        except Exception as e:
            logger.error(f"Cash refund processing error: {e}")
            raise

    def get_refund_status(self, refund_id):
        """Check status of a refund"""
        try:
            with get_db_cursor() as cursor:
                execute_with_retry(cursor, """
                    SELECT 
                        r.*,
                        o.order_number,
                        c.username as customer_name,
                        s.name as store_name
                    FROM refunds r
                    JOIN orders o ON r.order_id = o.id
                    JOIN customers c ON r.customer_id = c.id
                    JOIN stores s ON r.store_id = s.id
                    WHERE r.id = %s
                """, (refund_id,))
                
                refund = cursor.fetchone()
                
                if not refund:
                    raise ValueError(f"Refund {refund_id} not found")
                
                return {
                    'refund_id': refund['id'],
                    'order_number': refund['order_number'],
                    'customer_name': refund['customer_name'],
                    'store_name': refund['store_name'],
                    'amount': float(refund['amount']),
                    'status': refund['status'],
                    'reason': refund['reason'],
                    'payment_method': refund['payment_method'],
                    'refund_reference': refund['refund_reference'],
                    'created_at': refund['created_at'].isoformat() if refund['created_at'] else None,
                    'processed_at': refund['processed_at'].isoformat() if refund['processed_at'] else None
                }
                
        except Exception as e:
            logger.error(f"Get refund status error: {e}")
            raise

    def cancel_refund(self, refund_id, reason=""):
        """Cancel a pending refund"""
        try:
            with get_db_cursor() as cursor:
                # Check if refund can be cancelled
                execute_with_retry(cursor, """
                    SELECT status FROM refunds WHERE id = %s FOR UPDATE
                """, (refund_id,))
                
                refund = cursor.fetchone()
                
                if not refund:
                    raise ValueError(f"Refund {refund_id} not found")
                
                if refund['status'] not in ['pending', 'processing']:
                    raise ValueError(f"Cannot cancel refund with status: {refund['status']}")
                
                # Update refund status
                execute_with_retry(cursor, """
                    UPDATE refunds 
                    SET status = 'cancelled', 
                        cancellation_reason = %s,
                        processed_at = NOW()
                    WHERE id = %s
                """, (reason, refund_id))
                
                logger.info(f"Refund {refund_id} cancelled: {reason}")
                
                return {
                    'status': 'success',
                    'message': 'Refund cancelled successfully'
                }
                
        except Exception as e:
            logger.error(f"Cancel refund error: {e}")
            raise

    # Existing methods with improved error handling
    def _stripe_create_payment(self, amount, order_number, order_id, customer_email):
        """Create Stripe payment with enhanced error handling"""
        try:
            import stripe
            
            if not app.config.get('STRIPE_SECRET_KEY'):
                raise Exception("Stripe not configured")
                
            stripe.api_key = app.config['STRIPE_SECRET_KEY']
            
            payment_intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),
                currency='zar',
                metadata={
                    'order_number': order_number,
                    'order_id': order_id,
                    'customer_id': session.get('user_id')
                },
                receipt_email=customer_email,
                description=f"Order #{order_number}",
                statement_descriptor="CAMPUS MUNCHIES"
            )
            
            # Save transaction with enhanced data
            transaction_data = {
                'payment_intent_id': payment_intent.id,
                'client_secret': payment_intent.client_secret,
                'status': payment_intent.status,
                'currency': payment_intent.currency,
                'created': payment_intent.created
            }
            
            save_transaction(order_id, session.get('user_id'), 0, amount, 'card', 'pending', transaction_data)
            
            return {
                "payment_intent_id": payment_intent.id,
                "client_secret": payment_intent.client_secret,
                "status": payment_intent.status
            }
            
        except ImportError:
            logger.error("Stripe library not installed")
            raise
        except stripe.error.StripeError as e:
            logger.error(f"Stripe payment error: {e}")
            raise
        except Exception as e:
            logger.error(f"Stripe payment creation error: {e}")
            raise

    def _mpesa_stk_push(self, phone, amount, order_number, order_id):
        """Enhanced M-Pesa STK push with better error handling"""
        try:
            if not app.config.get('MPESA_CONSUMER_KEY'):
                raise Exception("M-Pesa not configured")
                
            formatted_phone = format_south_african_phone(phone)
            
            if not formatted_phone:
                raise ValueError("Invalid phone number format")
            
            access_token = self._mpesa_get_token()
            headers = {
                "Authorization": f"Bearer {access_token}", 
                "Content-Type": "application/json"
            }
            
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            password = base64.b64encode(
                f"{app.config['MPESA_SHORTCODE']}{app.config['MPESA_PASSKEY']}{timestamp}".encode()
            ).decode()
            
            payload = {
                "BusinessShortCode": app.config['MPESA_SHORTCODE'],
                "Password": password,
                "Timestamp": timestamp,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": str(int(amount)),
                "PartyA": formatted_phone,
                "PartyB": app.config['MPESA_SHORTCODE'],
                "PhoneNumber": formatted_phone,
                "CallBackURL": app.config['MPESA_CALLBACK_URL'],
                "AccountReference": order_number[:12],  # M-Pesa has character limits
                "TransactionDesc": f"Order {order_number}"
            }
            
            # Use retry mechanism for API calls
            @retry(stop_max_attempt_number=3, wait_exponential_multiplier=1000)
            def make_mpesa_request():
                response = requests.post(
                    "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
                    headers=headers, 
                    json=payload,
                    timeout=30
                )
                response.raise_for_status()
                return response.json()
            
            result = make_mpesa_request()
            
            # Save transaction with M-Pesa response data
            save_transaction(
                order_id, 
                session.get('user_id'), 
                0, 
                amount, 
                'mpesa', 
                'pending', 
                result
            )
            
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"M-Pesa API request error: {e}")
            raise
        except Exception as e:
            logger.error(f"M-Pesa STK push error: {e}")
            raise

    def _mpesa_get_token(self):
        """Get M-Pesa access token with caching"""
        cache_key = 'mpesa_token'
        token = cache.get(cache_key)
        
        if token:
            return token
            
        try:
            response = requests.get(
                "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",
                auth=(app.config['MPESA_CONSUMER_KEY'], app.config['MPESA_CONSUMER_SECRET']),
                timeout=10
            )
            response.raise_for_status()
            
            token_data = response.json()
            token = token_data.get('access_token')
            
            if not token:
                raise ValueError("No access token in M-Pesa response")
            
            # Cache token with slightly shorter expiry than actual token lifetime
            cache.set(cache_key, token, timeout=3500)  # 58 minutes
            
            return token
            
        except Exception as e:
            logger.error(f"M-Pesa token error: {e}")
            raise

payment_service = PaymentService()

@app.context_processor
def inject_defaults():
    return dict(
        csrf_token=generate_csrf,
        store_name=session.get('store_name', ''),
        stripe_public_key=app.config.get('STRIPE_PUBLIC_KEY', '')
    )

@app.route('/')
def home():
    if 'user_id' in session:
        if session.get('role') == 'customer':
            return redirect('/campusmunchies.com/')
        elif session.get('role') == 'admin':
            return redirect(f"/campusmunchies.com/admin/{session['store_name'].lower()}")
        elif session.get('role') == 'superadmin':
            return redirect('/campusmunchies.com/superadmin/')
    return redirect('/campusmunchies.com/login')

@app.route('/campusmunchies.com/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        username_or_email = bleach.clean(data.get('username', '').strip()[:100])
        password = data.get('password', '')[:100]

        if not username_or_email or not password:
            return jsonify({'error': 'Username/email and password required'}), 400

        try:
            with get_db_cursor() as cursor:
                # Check superadmin
                execute_with_retry(cursor, "SELECT * FROM superadmins WHERE username=%s OR email=%s",
                                   (username_or_email, username_or_email))
                superadmin = cursor.fetchone()
                if superadmin and check_password(password, superadmin['password_hash']):
                    session.update({
                        'user_id': superadmin['id'],
                        'username': superadmin['username'],
                        'role': 'superadmin'
                    })
                    session.permanent = True
                    return jsonify({
                        'success': True,
                        'redirect_url': '/campusmunchies.com/superadmin/',
                        'role': 'superadmin'
                    })

                # Check admin
                execute_with_retry(cursor, "SELECT * FROM admins WHERE username=%s OR email=%s",
                                   (username_or_email, username_or_email))
                admin = cursor.fetchone()
                if admin and check_password(password, admin['password_hash']):
                    execute_with_retry(cursor, "SELECT id, name FROM stores WHERE id=%s", (admin['store_id'],))
                    store = cursor.fetchone()
                    if not store:
                        return jsonify({'error': 'Invalid admin account'}), 401
                    
                    session.update({
                        'user_id': admin['id'],
                        'username': admin['username'],
                        'store_id': admin['store_id'],
                        'store_name': store['name'],
                        'role': 'admin'
                    })
                    session.permanent = True
                    
                   
                    store_slug = store['name'].lower().replace(' ', '-')
                    redirect_url = f"/campusmunchies.com/admin/{store_slug}/"
                    
                    return jsonify({
                        'success': True,
                        'redirect_url': redirect_url,
                        'role': 'admin',
                        'store_name': store['name']
                    })

                # Check customer
                execute_with_retry(cursor, "SELECT * FROM customers WHERE username=%s OR email=%s",
                                   (username_or_email, username_or_email))
                customer = cursor.fetchone()
                if customer and check_password(password, customer['password_hash']):
                    session.update({
                        'user_id': customer['id'],
                        'username': customer['username'],
                        'role': 'customer'
                    })
                    session.permanent = True
                    return jsonify({
                        'success': True,
                        'redirect_url': '/campusmunchies.com/',
                        'role': 'customer'
                    })

            return jsonify({'error': 'Invalid credentials'}), 401

        except Exception as e:
            logger.error(f"Login error: {e}")
            return jsonify({'error': 'Server error'}), 500

    return render_template('login.html')

@app.route('/campusmunchies.com/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    username = bleach.clean(data.get('username', '').strip()[:50])
    email = bleach.clean(data.get('email', '').strip()[:100])
    password = data.get('password', '')[:100]
    confirm_password = data.get('confirm_password', '')[:100]
    phone = bleach.clean(data.get('phone', '').strip()[:15])
    opt_in = data.get('opt_in', False)
    receive_sms = data.get('receive_sms', False)
    receive_emails = data.get('receive_emails', True)

    if not username or not email or not password or not confirm_password:
        return jsonify({'error': 'All required fields must be filled'}), 400

    if password != confirm_password:
        return jsonify({'error': 'Passwords do not match'}), 400

    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    if phone and not validate_south_african_phone(phone):
        return jsonify({'error': 'Please enter a valid South African phone number'}), 400

    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id FROM customers WHERE username=%s OR email=%s", (username, email))
            if cursor.fetchone():
                return jsonify({'error': 'Username or email already taken'}), 409

            password_hash = hash_password(password)
            execute_with_retry(cursor, """
                INSERT INTO customers (username, email, password_hash, phone, notifications_opt_in, receive_sms, receive_emails)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (username, email, password_hash, phone, int(opt_in), int(receive_sms), int(receive_emails)))
            customer_id = cursor.lastrowid

        if receive_emails:
            send_email(
                email,
                "Welcome to Campus Munchies!",
                f"Welcome {username}! Thank you for registering with Campus Munchies."
            )
        if receive_sms and phone and validate_south_african_phone(phone):
            sms_message = f"Welcome to Campus Munchies, {username}! Thank you for registering."
            send_sms(phone, sms_message)

        logger.info(f"New customer registered: {username}")
        return jsonify({
            'success': True, 
            'message': 'Registration successful!',
            'customer_id': customer_id
        })

    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/password/reset/request', methods=['POST'])
def request_password_reset():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    username_or_email = bleach.clean(data.get('username_or_email', '').strip()[:100])
    
    if not username_or_email:
        return jsonify({'error': 'Username or email required'}), 400

    try:
        with get_db_cursor() as cursor:
            user = None
            user_type = None
            
            # Check customers
            execute_with_retry(cursor, 
                "SELECT id, username, email FROM customers WHERE username=%s OR email=%s", 
                (username_or_email, username_or_email))
            customer = cursor.fetchone()
            if customer:
                user = customer
                user_type = 'customer'
            
            # Check admins
            if not user:
                execute_with_retry(cursor, 
                    "SELECT id, username, email FROM admins WHERE username=%s OR email=%s", 
                    (username_or_email, username_or_email))
                admin = cursor.fetchone()
                if admin:
                    user = admin
                    user_type = 'admin'
            
            # Check superadmins
            if not user:
                execute_with_retry(cursor, 
                    "SELECT id, username, email FROM superadmins WHERE username=%s OR email=%s", 
                    (username_or_email, username_or_email))
                superadmin = cursor.fetchone()
                if superadmin:
                    user = superadmin
                    user_type = 'superadmin'

            if not user:
                # Return success even if user not found for security
                return jsonify({
                    'success': True, 
                    'message': 'If an account exists with that username/email, a reset code has been sent.'
                })

            reset_token = str(uuid.uuid4())
            expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
            
            execute_with_retry(cursor, """
                INSERT INTO password_reset_tokens (user_id, user_type, token, expires_at)
                VALUES (%s, %s, %s, %s)
            """, (user['id'], user_type, reset_token, expires_at))
            
            reset_code = ''.join(random.choices(string.digits, k=6))
            
            cache_key = f'reset_code_{reset_token}'
            cache.set(cache_key, reset_code, timeout=3600)
            
            if user['email']:
                reset_link = f"{app.config['BASE_URL']}/campusmunchies.com/password/reset/confirm?token={reset_token}"
                subject = "Campus Munchies - Password Reset"
                body = f"""
Hello {user['username']},

You requested a password reset for your Campus Munchies account.

Your reset code is: {reset_code}

Alternatively, you can use this link to reset your password:
{reset_link}

This code/link will expire in 1 hour.

If you didn't request this reset, please ignore this email.

Best regards,
Campus Munchies Team
"""
                html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(45deg, #ff9a3d, #ff6b6b); color: white; padding: 20px; text-align: center; }}
        .content {{ background: #f9f9f9; padding: 20px; }}
        .code {{ font-size: 24px; font-weight: bold; text-align: center; color: #ff6b6b; margin: 20px 0; }}
        .button {{ display: inline-block; padding: 12px 24px; background: #ff9a3d; color: white; text-decoration: none; border-radius: 5px; }}
        .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Campus Munchies</h1>
            <h2>Password Reset</h2>
        </div>
        <div class="content">
            <p>Hello <strong>{user['username']}</strong>,</p>
            <p>You requested a password reset for your Campus Munchies account.</p>
            <div class="code">{reset_code}</div>
            <p>Enter this code on the password reset page, or click the button below:</p>
            <p style="text-align: center;">
                <a href="{reset_link}" class="button">Reset Password</a>
            </p>
            <p><em>This code/link will expire in 1 hour.</em></p>
            <p>If you didn't request this reset, please ignore this email.</p>
        </div>
        <div class="footer">
            <p>Best regards,<br>Campus Munchies Team</p>
        </div>
    </div>
</body>
</html>
"""
                send_email(user['email'], subject, body, html_body)

            logger.info(f"Password reset requested for {user_type}: {user['username']}")
            
            return jsonify({
                'success': True, 
                'message': 'If an account exists with that username/email, a reset code has been sent.',
                'reset_token': reset_token
            })

    except Exception as e:
        logger.error(f"Password reset request error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/password/reset/verify', methods=['POST'])
def verify_reset_code():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    reset_token = data.get('reset_token')
    reset_code = data.get('reset_code')
    
    if not reset_token or not reset_code:
        return jsonify({'error': 'Reset token and code required'}), 400

    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT user_id, user_type, expires_at 
                FROM password_reset_tokens 
                WHERE token=%s AND used=FALSE AND expires_at > NOW()
            """, (reset_token,))
            
            token_data = cursor.fetchone()
            if not token_data:
                return jsonify({'error': 'Invalid or expired reset token'}), 400

        cache_key = f'reset_code_{reset_token}'
        stored_code = cache.get(cache_key)
        
        if not stored_code or stored_code != reset_code:
            return jsonify({'error': 'Invalid reset code'}), 400

        return jsonify({
            'success': True, 
            'message': 'Code verified successfully',
            'user_type': token_data['user_type']
        })

    except Exception as e:
        logger.error(f"Reset code verification error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/password/reset/confirm', methods=['GET', 'POST'])
def confirm_password_reset():
    if request.method == 'GET':
        reset_token = request.args.get('token')
        if not reset_token:
            return "Invalid reset link", 400
        
        return render_template('reset_password_confirm.html', reset_token=reset_token)
    
    else:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        reset_token = data.get('reset_token')
        reset_code = data.get('reset_code')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')
        
        if not reset_token or not new_password or not confirm_password:
            return jsonify({'error': 'Reset token and passwords required'}), 400

        if new_password != confirm_password:
            return jsonify({'error': 'Passwords do not match'}), 400

        if len(new_password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400

        try:
            with get_db_cursor() as cursor:
                execute_with_retry(cursor, """
                    SELECT user_id, user_type, expires_at 
                    FROM password_reset_tokens 
                    WHERE token=%s AND used=FALSE AND expires_at > NOW()
                """, (reset_token,))
                
                token_data = cursor.fetchone()
                if not token_data:
                    return jsonify({'error': 'Invalid or expired reset token'}), 400

                if reset_code:
                    cache_key = f'reset_code_{reset_token}'
                    stored_code = cache.get(cache_key)
                    if not stored_code or stored_code != reset_code:
                        return jsonify({'error': 'Invalid reset code'}), 400

                user_id = token_data['user_id']
                user_type = token_data['user_type']
                
                password_hash = hash_password(new_password)
                
                if user_type == 'customer':
                    execute_with_retry(cursor, 
                        "UPDATE customers SET password_hash=%s WHERE id=%s", 
                        (password_hash, user_id))
                elif user_type == 'admin':
                    execute_with_retry(cursor, 
                        "UPDATE admins SET password_hash=%s WHERE id=%s", 
                        (password_hash, user_id))
                elif user_type == 'superadmin':
                    execute_with_retry(cursor, 
                        "UPDATE superadmins SET password_hash=%s WHERE id=%s", 
                        (password_hash, user_id))
                else:
                    return jsonify({'error': 'Invalid user type'}), 400

                execute_with_retry(cursor, 
                    "UPDATE password_reset_tokens SET used=TRUE WHERE token=%s", 
                    (reset_token,))
                
                cache_key = f'reset_code_{reset_token}'
                cache.delete(cache_key)
                
                logger.info(f"Password reset successful for {user_type}: {user_id}")

                return jsonify({
                    'success': True, 
                    'message': 'Password reset successfully! You can now login with your new password.'
                })

        except Exception as e:
            logger.error(f"Password reset confirmation error: {e}")
            return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/')
@login_required('customer')
def index():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT * FROM stores")
            stores = cursor.fetchall()
            
        return render_template('index.html', stores=stores)
    except Exception as e:
        logger.error(f"Index error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/stores')
@login_required('customer')
def get_stores():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id, name, description, avg_rating FROM stores")
            stores = cursor.fetchall()
        return jsonify({'success': True, 'stores': stores})
    except Exception as e:
        logger.error(f"Get stores error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/profile/status')
@login_required('customer')
def profile_status():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT username, email, phone 
                FROM customers 
                WHERE id=%s
            """, (session['user_id'],))
            customer = cursor.fetchone()
            
        return jsonify({'success': True, 'customer': customer})
    except Exception as e:
        logger.error(f"Profile status error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/menu/specials')
@login_required('customer')
def get_specials():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT mi.name, mi.description, mi.price, mi.image_url, s.name as store_name
                FROM menu_items mi
                JOIN stores s ON mi.store_id = s.id
                WHERE mi.is_special = TRUE AND mi.availability = TRUE
            """)
            specials = cursor.fetchall()
            
            # Convert Decimal to float for JSON serialization
            for special in specials:
                if 'price' in special and special['price'] is not None:
                    special['price'] = float(special['price'])
                    
        return jsonify({'success': True, 'specials': specials})
        
    except Exception as e:
        logger.error(f"Get specials error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/menu/all')
@login_required('customer')
def get_all_menu():
    try:
        query = request.args.get('q', '')
        with get_db_cursor() as cursor:
            if query:
                execute_with_retry(cursor, """
                    SELECT mi.name, mi.description, mi.price, mi.image_url, s.name as store_name
                    FROM menu_items mi
                    JOIN stores s ON mi.store_id = s.id
                    WHERE (mi.name LIKE %s OR mi.description LIKE %s) 
                    AND mi.availability = TRUE
                """, (f'%{query}%', f'%{query}%'))
            else:
                execute_with_retry(cursor, """
                    SELECT mi.name, mi.description, mi.price, mi.image_url, s.name as store_name
                    FROM menu_items mi
                    JOIN stores s ON mi.store_id = s.id
                    WHERE mi.availability = TRUE
                """)
            items = cursor.fetchall()
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        logger.error(f"Get all menu error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/profile')
@login_required('customer')
def profile():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT username, email, phone, receive_sms, receive_emails
                FROM customers 
                WHERE id=%s
            """, (session['user_id'],))
            profile_data = cursor.fetchone()
            
        return render_template('profile.html', profile=profile_data)
    except Exception as e:
        logger.error(f"Profile error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/profile/update', methods=['POST'])
@login_required('customer')
def update_profile():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    username = bleach.clean(data.get('username', '').strip()[:50])
    email = bleach.clean(data.get('email', '').strip()[:100])
    phone = bleach.clean(data.get('phone', '').strip()[:15])
    password = data.get('password', '')[:100]
    receive_sms = data.get('receive_sms', False)
    receive_emails = data.get('receive_emails', True)

    if phone and not validate_south_african_phone(phone):
        return jsonify({'error': 'Please enter a valid South African phone number'}), 400

    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, 
                "SELECT id FROM customers WHERE (username=%s OR email=%s) AND id != %s",
                (username, email, session['user_id']))
            if cursor.fetchone():
                return jsonify({'error': 'Username or email already taken'}), 409

            update_fields = []
            params = []
            
            if username:
                update_fields.append("username=%s")
                params.append(username)
            if email:
                update_fields.append("email=%s")
                params.append(email)
            if phone:
                update_fields.append("phone=%s")
                params.append(phone)
            
            update_fields.append("receive_sms=%s")
            params.append(int(receive_sms))
            update_fields.append("receive_emails=%s")
            params.append(int(receive_emails))

            if password:
                password_hash = hash_password(password)
                update_fields.append("password_hash=%s")
                params.append(password_hash)

            params.append(session['user_id'])
            
            if update_fields:
                query = f"UPDATE customers SET {', '.join(update_fields)} WHERE id=%s"
                execute_with_retry(cursor, query, params)

        return jsonify({'success': True, 'message': 'Profile updated successfully'})

    except Exception as e:
        logger.error(f"Update profile error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/<store_name>/menu')
@login_required('customer')
def get_menu(store_name):
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id, name FROM stores WHERE LOWER(name)=%s", (store_name.lower(),))
            store = cursor.fetchone()
            if not store:
                return "Store not found", 404
            execute_with_retry(cursor, """
                SELECT id, name, category, price, description, image_url, stock_quantity
                FROM menu_items
                WHERE store_id=%s AND availability=1
            """, (store['id'],))
            items = cursor.fetchall()
        return render_template('menu.html', store=store, menu=items)
    except Exception as e:
        logger.error(f"Get menu error: {e}")
        return jsonify({'error': 'Server error'}), 500

def sanitize_input(value, default='', max_length=None):
    """Safely sanitize input values that might be None"""
    if value is None:
        return default
    
    try:
        cleaned = str(value).strip()
        cleaned = bleach.clean(cleaned)
        if max_length and len(cleaned) > max_length:
            cleaned = cleaned[:max_length]
        return cleaned
    except Exception:
        return default

@app.route('/campusmunchies.com/<store_name>/checkout')
@login_required('customer')
def checkout(store_name):
    """Render checkout page with store and payment configuration."""
    try:
        with get_db_cursor(dictionary=True) as cursor:
            execute_with_retry(
                cursor,
                "SELECT id, name FROM stores WHERE LOWER(name)=%s",
                (store_name.lower(),)
            )
            store = cursor.fetchone()

        if not store:
            logger.warning(f"Checkout attempted for non-existent store: {store_name}")
            return render_template('404.html', message='Store not found'), 404

        return render_template(
            'checkout.html',
            store=store,
            stripe_public_key=app.config.get('STRIPE_PUBLIC_KEY', '')
        )

    except Exception as e:
        logger.error(f"Checkout error for {store_name}: {e}", exc_info=True)
        return render_template('error.html', message='Server error during checkout'), 500

@app.route('/campusmunchies.com/<store_name>/order/create', methods=['POST'])
@login_required('customer')
def create_order(store_name):
    """Create a new order with comprehensive validation and error handling"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON data"}), 400

        items = data.get('items', [])
        payment_method = data.get('payment_method', 'cash')
        order_type_input = data.get('delivery_option', 'pickup')
        phone = (data.get('phone') or '').strip()
        delivery_address = (data.get('delivery_address') or '').strip()


        if not items:
            return jsonify({"error": "No items provided"}), 400

        valid_payment_methods = ['cash', 'card', 'mpesa']
        if payment_method not in valid_payment_methods:
            return jsonify({"error": f"Invalid payment method. Must be one of: {', '.join(valid_payment_methods)}"}), 400

        order_type_map = {
            'pickup': 'pickup',
            'delivery': 'delivery',
            'sit_in': 'sit_in',
            'sit-in': 'sit_in',
            'sit in': 'sit_in'
        }
        order_type = order_type_map.get(order_type_input.lower(), 'pickup')

        # Prevent cash payment with delivery address
        if payment_method == 'cash' and delivery_address:
            return jsonify({"error": "Cash payment cannot be used with delivery address. Please choose pickup or sit-in option."}), 400

        # Payment method specific validations
        if payment_method == 'mpesa':
            if not phone:
                return jsonify({"error": "Phone number required for M-Pesa payments"}), 400
            if not validate_south_african_phone(phone):
                return jsonify({"error": "Please enter a valid South African phone number for M-Pesa"}), 400
        
        # Delivery specific validations
        if order_type == 'delivery':
            if not delivery_address:
                return jsonify({"error": "Delivery address required for delivery orders"}), 400
            if payment_method == 'cash':
                return jsonify({"error": "Cash payment not allowed for delivery orders. Please use card or M-Pesa."}), 400
        
        # Card payment validations
        if payment_method == 'card' and order_type == 'delivery' and not delivery_address:
            return jsonify({"error": "Delivery address required for card payment delivery orders"}), 400

        customer_id = session['user_id']

        with get_db_cursor(dictionary=True) as cursor:
            execute_with_retry(cursor, 
                "SELECT id, name FROM stores WHERE LOWER(name)=%s", 
                (store_name.lower(),))
            store = cursor.fetchone()
            
            if not store:
                return jsonify({"error": "Store not found"}), 404
            
            store_id = store['id']
            store_name_display = store['name']

        total = 0.0
        validated_items = []
        order_items_data = []

        with get_db_cursor(dictionary=True) as cursor:
            for item in items:
                item_id = item.get('id')
                quantity = int(item.get('quantity', 1))
                
                if not item_id or quantity < 1:
                    return jsonify({"error": f"Invalid item data: ID required and quantity must be at least 1"}), 400

                execute_with_retry(cursor, """
                    SELECT id, name, price, stock_quantity, availability 
                    FROM menu_items 
                    WHERE id=%s AND store_id=%s AND availability=1
                    FOR UPDATE
                """, (item_id, store_id))
                
                menu_item = cursor.fetchone()
                if not menu_item:
                    return jsonify({"error": f"Item ID {item_id} not available or not found in {store_name_display}"}), 400

                if menu_item['stock_quantity'] < quantity:
                    return jsonify({
                        "error": f"Not enough stock for '{menu_item['name']}'. Only {menu_item['stock_quantity']} available, but {quantity} requested."
                    }), 400

                item_price = float(menu_item['price'])
                item_total = item_price * quantity
                total += item_total
                
                validated_items.append({
                    'id': menu_item['id'],
                    'name': menu_item['name'],
                    'quantity': quantity,
                    'price': item_price,
                    'total': item_total
                })
                
                order_items_data.append((menu_item['id'], quantity, item_price))

        if total <= 0:
            return jsonify({"error": "Order total must be greater than 0"}), 400

        order_number = str(uuid.uuid4()).replace('-', '').upper()[:12]
        status = 'confirmed' if payment_method == 'cash' else 'pending'

        try:
            with get_db_cursor(dictionary=True) as cursor:
                # Start transaction
                cursor.execute("START TRANSACTION")

                # Insert order - only include delivery_address if order_type is delivery
                execute_with_retry(cursor, """
                    INSERT INTO orders (customer_id, store_id, amount, status, order_number, 
                                      payment_method, order_type, delivery_address)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (customer_id, store_id, total, status, order_number, payment_method, 
                      order_type, delivery_address if order_type == 'delivery' else None))
                
                order_id = cursor.lastrowid

                # Insert order items
                for item_id, quantity, price in order_items_data:
                    execute_with_retry(cursor, """
                        INSERT INTO order_items (order_id, item_id, quantity, price)
                        VALUES (%s, %s, %s, %s)
                    """, (order_id, item_id, quantity, price))

                    # Update stock
                    execute_with_retry(cursor, """
                        UPDATE menu_items 
                        SET stock_quantity = stock_quantity - %s 
                        WHERE id = %s
                    """, (quantity, item_id))

                # Create transaction record
                transaction_status = 'completed' if payment_method == 'cash' else 'pending'
                execute_with_retry(cursor, """
                    INSERT INTO transactions (order_id, customer_id, store_id, amount, 
                                           payment_method, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (order_id, customer_id, store_id, total, payment_method, transaction_status))

                # Create notification
                execute_with_retry(cursor, """
                    INSERT INTO notifications (customer_id, order_id, type, message)
                    VALUES (%s, %s, %s, %s)
                """, (customer_id, order_id, 'order_update', 
                      f"Order #{order_number} placed successfully. Status: {status}"))

                cursor.execute("COMMIT")

        except Exception as db_error:
            if 'cursor' in locals():
                cursor.execute("ROLLBACK")
            logger.error(f"Database transaction error for order creation: {db_error}")
            return jsonify({"error": "Failed to create order due to database error"}), 500

        # Send confirmation if customer details available
        try:
            with get_db_cursor(dictionary=True) as cursor:
                execute_with_retry(cursor, """
                    SELECT email, phone FROM customers WHERE id=%s
                """, (customer_id,))
                customer = cursor.fetchone()
                
                if customer:
                    send_order_confirmation(
                        customer['email'],
                        customer['phone'],
                        {
                            'order_number': order_number,
                            'total': total,
                            'store_name': store_name_display,
                            'status': status
                        }
                    )
        except Exception as notification_error:
            logger.error(f"Failed to send order confirmation: {notification_error}")

        logger.info(f"Order created successfully: #{order_number} for customer {customer_id}, total: R{total:.2f}")

        return jsonify({
            "success": True,
            "message": f"Order #{order_number} placed successfully",
            "order_id": order_id,
            "order_number": order_number,
            "store": store_name_display,
            "total": total,
            "payment_method": payment_method,
            "order_type": order_type,
            "status": status,
            "items": validated_items
        })

    except ValueError as ve:
        logger.warning(f"Order validation error: {ve}")
        return jsonify({'error': f'Validation error: {str(ve)}'}), 400
    except Exception as e:
        logger.error(f"Unexpected order creation error: {e}", exc_info=True)
        return jsonify({'error': 'An unexpected error occurred while creating your order. Please try again.'}), 500

@app.route('/campusmunchies.com/cart')
@login_required('customer')
def view_cart():
    """Display the shopping cart page"""
    return render_template('cart.html')

@app.route('/campusmunchies.com/api/cart', methods=['GET'])
@login_required('customer')
def get_cart():
    """Get current user's cart items"""
    try:
        customer_id = session['user_id']
        
        with get_db_cursor(dictionary=True) as cursor:
            # Get cart items with menu item details
            execute_with_retry(cursor, """
                SELECT 
                    c.id as cart_id,
                    c.item_id,
                    c.quantity,
                    c.notes,
                    c.created_at,
                    mi.name,
                    mi.price,
                    mi.image_url,
                    mi.stock_quantity,
                    mi.availability,
                    s.id as store_id,
                    s.name as store_name
                FROM cart c
                JOIN menu_items mi ON c.item_id = mi.id
                JOIN stores s ON mi.store_id = s.id
                WHERE c.customer_id = %s
                ORDER BY c.created_at DESC
            """, (customer_id,))
            
            cart_items = cursor.fetchall()
            
            # Group items by store
            stores = {}
            total_amount = 0
            total_items = 0
            
            for item in cart_items:
                store_id = item['store_id']
                if store_id not in stores:
                    stores[store_id] = {
                        'store_id': store_id,
                        'store_name': item['store_name'],
                        'items': [],
                        'subtotal': 0
                    }
                
                item_total = float(item['price']) * item['quantity']
                stores[store_id]['subtotal'] += item_total
                stores[store_id]['items'].append({
                    'cart_id': item['cart_id'],
                    'item_id': item['item_id'],
                    'name': item['name'],
                    'price': float(item['price']),
                    'quantity': item['quantity'],
                    'notes': item['notes'],
                    'image_url': item['image_url'],
                    'stock_quantity': item['stock_quantity'],
                    'availability': bool(item['availability']),
                    'item_total': item_total,
                    'max_quantity': min(item['stock_quantity'], 10)  # Limit to 10 or available stock
                })
                
                total_amount += item_total
                total_items += item['quantity']
            
        return jsonify({
            'success': True,
            'cart': {
                'stores': list(stores.values()),
                'total_amount': total_amount,
                'total_items': total_items
            }
        })
        
    except Exception as e:
        logger.error(f"Get cart error: {e}")
        return jsonify({'error': 'Failed to load cart'}), 500

@app.route('/campusmunchies.com/api/cart/add', methods=['POST'])
@login_required('customer')
def add_to_cart():
    """Add item to cart"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400
        
        item_id = data.get('item_id')
        quantity = int(data.get('quantity', 1))
        notes = bleach.clean(data.get('notes', '').strip()[:200])
        
        if not item_id:
            return jsonify({'error': 'Item ID is required'}), 400
        
        if quantity < 1:
            return jsonify({'error': 'Quantity must be at least 1'}), 400
        
        customer_id = session['user_id']
        
        with get_db_cursor(dictionary=True) as cursor:
            # Check if item exists and is available
            execute_with_retry(cursor, """
                SELECT mi.id, mi.name, mi.price, mi.stock_quantity, mi.availability, 
                       mi.store_id, s.name as store_name
                FROM menu_items mi
                JOIN stores s ON mi.store_id = s.id
                WHERE mi.id = %s AND mi.availability = 1
            """, (item_id,))
            
            menu_item = cursor.fetchone()
            if not menu_item:
                return jsonify({'error': 'Item not available or not found'}), 404
            
            # Check stock availability
            if menu_item['stock_quantity'] < quantity:
                return jsonify({
                    'error': f"Only {menu_item['stock_quantity']} items available in stock"
                }), 400

            execute_with_retry(cursor, """
                SELECT id, quantity FROM cart 
                WHERE customer_id = %s AND item_id = %s
            """, (customer_id, item_id))
            
            existing_item = cursor.fetchone()
            
            if existing_item:
                new_quantity = existing_item['quantity'] + quantity

                if new_quantity > menu_item['stock_quantity']:
                    return jsonify({
                        'error': f"Cannot add {quantity} more. Maximum available is {menu_item['stock_quantity'] - existing_item['quantity']}"
                    }), 400
                
                execute_with_retry(cursor, """
                    UPDATE cart 
                    SET quantity = %s, notes = %s, created_at = NOW()
                    WHERE id = %s
                """, (new_quantity, notes, existing_item['id']))
                
                action = 'updated'
                cart_id = existing_item['id']
                final_quantity = new_quantity
                
            else:
                execute_with_retry(cursor, """
                    INSERT INTO cart (customer_id, store_id, item_id, quantity, notes)
                    VALUES (%s, %s, %s, %s, %s)
                """, (customer_id, menu_item['store_id'], item_id, quantity, notes))
                
                action = 'added'
                cart_id = cursor.lastrowid
                final_quantity = quantity
            execute_with_retry(cursor, """
                SELECT COUNT(*) as item_count, SUM(quantity) as total_quantity
                FROM cart 
                WHERE customer_id = %s
            """, (customer_id,))
            
            cart_stats = cursor.fetchone()
            
        logger.info(f"Item {item_id} {action} to cart for customer {customer_id}")
        
        return jsonify({
            'success': True,
            'message': f'Item {action} to cart successfully',
            'cart_id': cart_id,
            'quantity': final_quantity,
            'cart_stats': {
                'item_count': cart_stats['item_count'],
                'total_quantity': cart_stats['total_quantity'] or 0
            }
        })
        
    except ValueError:
        return jsonify({'error': 'Invalid quantity format'}), 400
    except Exception as e:
        logger.error(f"Add to cart error: {e}")
        return jsonify({'error': 'Failed to add item to cart'}), 500

@app.route('/campusmunchies.com/api/cart/update/<int:cart_id>', methods=['PUT'])
@login_required('customer')
def update_cart_item(cart_id):
    """Update cart item quantity or notes"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400
        
        quantity = data.get('quantity')
        notes = data.get('notes')
        
        customer_id = session['user_id']
        
        with get_db_cursor(dictionary=True) as cursor:
            # Verify cart item belongs to user and get item details
            execute_with_retry(cursor, """
                SELECT c.item_id, c.quantity, mi.stock_quantity, mi.name
                FROM cart c
                JOIN menu_items mi ON c.item_id = mi.id
                WHERE c.id = %s AND c.customer_id = %s
            """, (cart_id, customer_id))
            
            cart_item = cursor.fetchone()
            if not cart_item:
                return jsonify({'error': 'Cart item not found'}), 404
            
            update_fields = []
            params = []
            
            if quantity is not None:
                new_quantity = int(quantity)
                if new_quantity < 0:
                    return jsonify({'error': 'Quantity cannot be negative'}), 400
                elif new_quantity == 0:
                    # Remove item if quantity is 0
                    return remove_from_cart(cart_id)
                
                # Check stock availability
                if new_quantity > cart_item['stock_quantity']:
                    return jsonify({
                        'error': f"Only {cart_item['stock_quantity']} items available for {cart_item['name']}"
                    }), 400
                
                update_fields.append("quantity = %s")
                params.append(new_quantity)
            
            if notes is not None:
                cleaned_notes = bleach.clean(notes.strip()[:200])
                update_fields.append("notes = %s")
                params.append(cleaned_notes)
            
            if update_fields:
                update_fields.append("created_at = NOW()")
                params.extend([cart_id, customer_id])
                
                execute_with_retry(cursor, f"""
                    UPDATE cart 
                    SET {', '.join(update_fields)}
                    WHERE id = %s AND customer_id = %s
                """, params)
                
                if cursor.rowcount == 0:
                    return jsonify({'error': 'Failed to update cart item'}), 400
            
            # Get updated cart stats
            execute_with_retry(cursor, """
                SELECT COUNT(*) as item_count, SUM(quantity) as total_quantity
                FROM cart 
                WHERE customer_id = %s
            """, (customer_id,))
            
            cart_stats = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'message': 'Cart item updated successfully',
            'cart_stats': {
                'item_count': cart_stats['item_count'],
                'total_quantity': cart_stats['total_quantity'] or 0
            }
        })
        
    except ValueError:
        return jsonify({'error': 'Invalid quantity format'}), 400
    except Exception as e:
        logger.error(f"Update cart item error: {e}")
        return jsonify({'error': 'Failed to update cart item'}), 500

@app.route('/campusmunchies.com/api/cart/remove/<int:cart_id>', methods=['DELETE'])
@login_required('customer')
def remove_from_cart(cart_id):
    """Remove item from cart"""
    try:
        customer_id = session['user_id']
        
        with get_db_cursor(dictionary=True) as cursor:
            # Verify cart item belongs to user before deleting
            execute_with_retry(cursor, """
                DELETE FROM cart 
                WHERE id = %s AND customer_id = %s
            """, (cart_id, customer_id))
            
            if cursor.rowcount == 0:
                return jsonify({'error': 'Cart item not found'}), 404
            
            # Get updated cart stats
            execute_with_retry(cursor, """
                SELECT COUNT(*) as item_count, SUM(quantity) as total_quantity
                FROM cart 
                WHERE customer_id = %s
            """, (customer_id,))
            
            cart_stats = cursor.fetchone()
        
        logger.info(f"Item {cart_id} removed from cart for customer {customer_id}")
        
        return jsonify({
            'success': True,
            'message': 'Item removed from cart successfully',
            'cart_stats': {
                'item_count': cart_stats['item_count'],
                'total_quantity': cart_stats['total_quantity'] or 0
            }
        })
        
    except Exception as e:
        logger.error(f"Remove from cart error: {e}")
        return jsonify({'error': 'Failed to remove item from cart'}), 500

@app.route('/campusmunchies.com/api/cart/clear', methods=['DELETE'])
@login_required('customer')
def clear_cart():
    """Clear all items from cart"""
    try:
        customer_id = session['user_id']
        
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                DELETE FROM cart 
                WHERE customer_id = %s
            """, (customer_id,))
            
            items_removed = cursor.rowcount
        
        logger.info(f"Cart cleared for customer {customer_id}, {items_removed} items removed")
        
        return jsonify({
            'success': True,
            'message': f'Cart cleared successfully ({items_removed} items removed)'
        })
        
    except Exception as e:
        logger.error(f"Clear cart error: {e}")
        return jsonify({'error': 'Failed to clear cart'}), 500

@app.route('/campusmunchies.com/api/cart/count')
@login_required('customer')
def get_cart_count():
    """Get cart item count for badge display"""
    try:
        customer_id = session['user_id']
        
        with get_db_cursor(dictionary=True) as cursor:
            execute_with_retry(cursor, """
                SELECT COUNT(*) as item_count, SUM(quantity) as total_quantity
                FROM cart 
                WHERE customer_id = %s
            """, (customer_id,))
            
            result = cursor.fetchone()
        
        return jsonify({
            'success': True,
            'item_count': result['item_count'] or 0,
            'total_quantity': result['total_quantity'] or 0
        })
        
    except Exception as e:
        logger.error(f"Get cart count error: {e}")
        return jsonify({'error': 'Failed to get cart count'}), 500

@app.route('/campusmunchies.com/api/cart/move-to-cart/<int:item_id>', methods=['POST'])
@login_required('customer')
def move_to_cart_from_session(item_id):
    """Move item from session (reorder) to persistent cart"""
    try:
        customer_id = session['user_id']
        
        # Check if item exists in session reorder data
        reorder_items = session.get('reorder_items', [])
        reorder_item = next((item for item in reorder_items if item['id'] == item_id), None)
        
        if not reorder_item:
            return jsonify({'error': 'Item not found in reorder data'}), 404
        
        # Add to cart using existing function
        cart_data = {
            'item_id': item_id,
            'quantity': reorder_item['quantity']
        }
        
        # Use the add_to_cart logic
        with get_db_cursor(dictionary=True) as cursor:
            # Check if item exists and is available
            execute_with_retry(cursor, """
                SELECT id, store_id, stock_quantity, availability 
                FROM menu_items 
                WHERE id = %s AND availability = 1
            """, (item_id,))
            
            menu_item = cursor.fetchone()
            if not menu_item:
                return jsonify({'error': 'Item no longer available'}), 404
            
            # Add to cart
            execute_with_retry(cursor, """
                INSERT INTO cart (customer_id, store_id, item_id, quantity)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                quantity = quantity + VALUES(quantity),
                created_at = NOW()
            """, (customer_id, menu_item['store_id'], item_id, reorder_item['quantity']))
        
        # Remove from session
        session['reorder_items'] = [item for item in reorder_items if item['id'] != item_id]
        
        return jsonify({
            'success': True,
            'message': 'Item moved to cart successfully'
        })
        
    except Exception as e:
        logger.error(f"Move to cart error: {e}")
        return jsonify({'error': 'Failed to move item to cart'}), 500
      
@app.route('/campusmunchies.com/orders/<int:order_id>')
@login_required('customer')
def order_detail(order_id):
    """Order detail page - returns HTML"""
    try:
        with get_db_cursor(dictionary=True) as cursor:
            execute_with_retry(cursor, """
                SELECT 
                    o.*, 
                    s.name AS store_name, 
                    c.username, c.email, c.phone,
                    o.order_type AS delivery_option
                FROM orders o
                JOIN stores s ON o.store_id = s.id
                JOIN customers c ON o.customer_id = c.id
                WHERE o.id=%s AND o.customer_id=%s
            """, (order_id, session['user_id']))

            order = cursor.fetchone()
            if not order:
                return render_template('404.html', message='Order not found'), 404

            execute_with_retry(cursor, """
                SELECT mi.name, oi.quantity, oi.price, mi.id AS item_id
                FROM order_items oi
                JOIN menu_items mi ON oi.item_id = mi.id
                WHERE oi.order_id=%s
            """, (order_id,))
            
            items = cursor.fetchall()

            order_dict = dict(order)
            order_dict['items'] = items
            order_dict['amount'] = float(order_dict.get('amount', 0.0))
            
            # Ensure created_at is properly formatted
            if order_dict.get('created_at'):
                order_dict['created_at'] = order_dict['created_at']

        return render_template('order_detail.html', order=order_dict)

    except Exception as e:
        logger.error(f"Order detail error: {e}")
        return render_template('error.html', message='Server error loading order details'), 500
    

@app.route('/campusmunchies.com/orders')
@login_required('customer')
def orders():
    """Orders page - returns HTML template"""
    return render_template('order.html')

@app.route('/campusmunchies.com/api/orders')
@login_required('customer')
def orders_api():
    """Return orders as JSON for the frontend"""
    customer_id = session['user_id']
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT o.id, o.order_number, o.amount, o.payment_method, o.order_type, 
                       o.status, o.created_at, s.name as store_name
                FROM orders o
                JOIN stores s ON o.store_id = s.id
                WHERE o.customer_id = %s
                ORDER BY o.created_at DESC
            """, (customer_id,))
            db_orders = cursor.fetchall()

            orders = []
            for order in db_orders:
                execute_with_retry(cursor, """
                    SELECT mi.name, oi.quantity, oi.price
                    FROM order_items oi
                    JOIN menu_items mi ON oi.item_id = mi.id
                    WHERE oi.order_id = %s
                """, (order['id'],))
                items = cursor.fetchall()
                
                order_data = {
                    'id': order['id'],
                    'order_number': order['order_number'],
                    'amount': float(order['amount']) if order['amount'] else 0.0,
                    'payment_method': order['payment_method'],
                    'order_type': order['order_type'],
                    'status': order['status'],
                    'created_at': order['created_at'].isoformat() if order['created_at'] else None,
                    'store_name': order['store_name'],
                    'items': items  # This was missing
                }
                orders.append(order_data)

        return jsonify(orders)  # Return the list directly, not wrapped in success
        
    except Exception as e:
        logger.error(f"Orders API error: {e}")
        return jsonify({'error': 'An error occurred while loading your orders'}), 500

@app.route('/campusmunchies.com/api/order/<int:order_id>')
@login_required('customer')
def get_single_order(order_id):
    """Get a single order by ID"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT o.*, s.name as store_name
                FROM orders o
                JOIN stores s ON o.store_id = s.id
                WHERE o.id=%s AND o.customer_id=%s
            """, (order_id, session['user_id']))
            
            order = cursor.fetchone()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            execute_with_retry(cursor, """
                SELECT mi.name, oi.quantity, oi.price
                FROM order_items oi
                JOIN menu_items mi ON oi.item_id = mi.id
                WHERE oi.order_id=%s
            """, (order_id,))
            items = cursor.fetchall()

            order_data = {
                'id': order['id'],
                'order_number': order['order_number'],
                'amount': float(order['amount']),
                'payment_method': order['payment_method'],
                'order_type': order['order_type'],
                'status': order['status'],
                'created_at': order['created_at'].isoformat() if order['created_at'] else None,
                'store_name': order['store_name'],
                'items': items
            }

        return jsonify(order_data)

    except Exception as e:
        logger.error(f"Get single order error: {e}")
        return jsonify({'error': 'Server error'}), 500
    
@app.route('/campusmunchies.com/orders/<int:order_id>/cancel', methods=['POST'])
@login_required('customer')
def cancel_order(order_id):
    data = request.get_json() or {}
    reason = bleach.clean(data.get('reason', '').strip()[:500])
    action = data.get('action', 'refund')  # refund, donate, or credit

    if not reason:
        return jsonify({'error': 'Cancellation reason required'}), 400

    try:
        with get_db_cursor(dictionary=True) as cursor:
            execute_with_retry(cursor, """
                SELECT o.id, o.status, o.order_number, o.store_id, o.customer_id, o.amount,
                       o.payment_method, t.id as transaction_id, t.status as transaction_status
                FROM orders o
                LEFT JOIN transactions t ON o.id = t.order_id
                WHERE o.id=%s AND o.customer_id=%s
            """, (order_id, session['user_id']))
            
            order = cursor.fetchone()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            # Validate if order can be cancelled
            if order['status'] in ['cancelled', 'completed', 'delivered']:
                return jsonify({'error': 'Cannot cancel a completed, delivered, or already cancelled order'}), 400
            
            if order['status'] in ['ready']:
                return jsonify({'error': 'Order is already being prepared. Please contact the store directly.'}), 400

            # Start transaction
            cursor.execute("START TRANSACTION")

            # Update order status
            execute_with_retry(cursor, """
                UPDATE orders 
                SET status='cancelled', cancellation_reason=%s, cancelled_at=NOW()
                WHERE id=%s
            """, (reason, order_id))

            # Restore stock quantities
            execute_with_retry(cursor, """
                UPDATE menu_items mi
                JOIN order_items oi ON mi.id = oi.item_id
                SET mi.stock_quantity = mi.stock_quantity + oi.quantity
                WHERE oi.order_id=%s
            """, (order_id,))

            # Handle payment refund based on action and payment method
            if order['transaction_status'] == 'completed' and order['amount'] > 0:
                if action == 'refund':
                    # Process refund based on payment method
                    if order['payment_method'] in ['card', 'mpesa']:
                        try:
                            # Use payment service to process refund
                            refund_result = payment_service.process_refund(
                                order_id, 
                                None,  # Full refund
                                f"Order cancellation: {reason}"
                            )
                            
                            # Update transaction status - use valid status values
                            execute_with_retry(cursor, """
                                UPDATE transactions 
                                SET status='refunded'
                                WHERE order_id=%s
                            """, (order_id,))
                            
                        except Exception as refund_error:
                            logger.error(f"Automatic refund failed: {refund_error}")
                            # Mark for manual refund processing - use valid status values
                            execute_with_retry(cursor, """
                                UPDATE transactions 
                                SET status='pending_refund'
                                WHERE order_id=%s
                            """, (order_id,))
                    
                    elif order['payment_method'] == 'cash':
                        # For cash payments, just update transaction status
                        execute_with_retry(cursor, """
                            UPDATE transactions 
                            SET status='cancelled'
                            WHERE order_id=%s
                        """, (order_id,))
                
                elif action == 'donate':
                    # Mark as donated - no refund
                    execute_with_retry(cursor, """
                        UPDATE transactions 
                        SET status='completed'  # Use existing status instead of 'donated'
                        WHERE order_id=%s
                    """, (order_id,))
                    
                    # Record donation
                    execute_with_retry(cursor, """
                        INSERT INTO donations (order_id, store_id, amount, reason)
                        VALUES (%s, %s, %s, %s)
                    """, (order_id, order['store_id'], order['amount'], reason))
                
                elif action == 'credit':
                    # Store credit for future use
                    execute_with_retry(cursor, """
                        UPDATE transactions 
                        SET status='completed'  # Use existing status instead of 'credited'
                        WHERE order_id=%s
                    """, (order_id,))
                    
                    # Add store credit to customer account
                    execute_with_retry(cursor, """
                        INSERT INTO customer_credits (customer_id, amount, source_order_id, reason)
                        VALUES (%s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE 
                        amount = amount + VALUES(amount)
                    """, (session['user_id'], order['amount'], order_id, f"Credit from cancelled order #{order['order_number']}"))

            # Commit transaction
            cursor.execute("COMMIT")

        # Create notification
        create_notification(
            session['user_id'], 
            order_id, 
            'order_cancelled', 
            f"Order {order['order_number']} has been cancelled. Reason: {reason}"
        )

        # Send status update to customer
        try:
            with get_db_cursor(dictionary=True) as cursor:
                execute_with_retry(cursor, """
                    SELECT c.email, c.phone, s.name as store_name
                    FROM customers c
                    JOIN orders o ON c.id = o.customer_id
                    JOIN stores s ON o.store_id = s.id
                    WHERE o.id=%s
                """, (order_id,))
                customer_data = cursor.fetchone()
                
                if customer_data:
                    send_order_status_update(
                        customer_data['email'],
                        customer_data['phone'],
                        order['order_number'],
                        'cancelled',
                        customer_data['store_name']
                    )
        except Exception as email_error:
            logger.error(f"Failed to send cancellation email: {email_error}")

        # Prepare response message based on action
        message = f"Order {order['order_number']} cancelled successfully."
        
        if order['amount'] > 0:
            if action == 'refund':
                if order['payment_method'] in ['card', 'mpesa']:
                    message += " Refund will be processed within 3-5 business days."
                else:
                    message += " Please contact the store for cash refund."
            elif action == 'donate':
                message += " The order amount has been donated to the store."
            elif action == 'credit':
                message += f" R{order['amount']:.2f} has been added to your account credit."

        return jsonify({'success': True, 'message': message})

    except Exception as e:
        # Rollback in case of error with proper exception handling
        logger.error(f"Cancel order error: {e}")
        try:
            if 'cursor' in locals():
                cursor.execute("ROLLBACK")
        except Exception as rollback_error:
            logger.error(f"Rollback also failed: {rollback_error}")
        return jsonify({'error': 'Server error occurred while cancelling order'}), 500
      
@app.route('/campusmunchies.com/orders/<int:order_id>/reorder', methods=['POST'])
@login_required('customer')
def reorder(order_id):
    try:
        with get_db_cursor(dictionary=True) as cursor:
            # Get order details and items
            execute_with_retry(cursor, """
                SELECT o.store_id, s.name as store_name, oi.item_id, oi.quantity, oi.price,
                       mi.name as item_name, mi.stock_quantity, mi.availability
                FROM orders o
                JOIN stores s ON o.store_id = s.id
                JOIN order_items oi ON o.id = oi.order_id
                JOIN menu_items mi ON oi.item_id = mi.id
                WHERE o.id=%s AND o.customer_id=%s
            """, (order_id, session['user_id']))
            
            order_items = cursor.fetchall()
            if not order_items:
                return jsonify({'error': 'Order not found'}), 404

            store_id = order_items[0]['store_id']
            store_name = order_items[0]['store_name']

            available_items = []
            unavailable_items = []
            added_to_cart = []
            
            for item in order_items:
                # Check if item is still available
                if item['availability'] and item['stock_quantity'] >= item['quantity']:
                    # Check if item already exists in cart
                    execute_with_retry(cursor, """
                        SELECT id, quantity FROM cart 
                        WHERE customer_id = %s AND item_id = %s
                    """, (session['user_id'], item['item_id']))
                    
                    existing_item = cursor.fetchone()
                    
                    if existing_item:
                        # Update quantity, but don't exceed available stock
                        new_quantity = existing_item['quantity'] + item['quantity']
                        max_quantity = min(new_quantity, item['stock_quantity'])
                        
                        execute_with_retry(cursor, """
                            UPDATE cart 
                            SET quantity = %s, created_at = NOW()
                            WHERE id = %s
                        """, (max_quantity, existing_item['id']))
                        
                        added_to_cart.append({
                            'id': item['item_id'],
                            'name': item['item_name'],
                            'quantity': item['quantity'],
                            'action': 'updated',
                            'final_quantity': max_quantity
                        })
                    else:
                        # Add new item to cart
                        execute_with_retry(cursor, """
                            INSERT INTO cart (customer_id, store_id, item_id, quantity)
                            VALUES (%s, %s, %s, %s)
                        """, (session['user_id'], store_id, item['item_id'], item['quantity']))
                        
                        added_to_cart.append({
                            'id': item['item_id'],
                            'name': item['item_name'],
                            'quantity': item['quantity'],
                            'action': 'added'
                        })
                    
                    available_items.append({
                        'id': item['item_id'],
                        'name': item['item_name'],
                        'price': float(item['price']),
                        'quantity': item['quantity'],
                        'available': True,
                        'added_to_cart': True
                    })
                else:
                    # Item not available or insufficient stock
                    available_quantity = min(item['quantity'], item['stock_quantity']) if item['availability'] else 0
                    
                    unavailable_items.append({
                        'id': item['item_id'],
                        'name': item['item_name'],
                        'requested_quantity': item['quantity'],
                        'available_quantity': available_quantity,
                        'available': False
                    })
                    
                    # Add available quantity to cart if any
                    if available_quantity > 0:
                        execute_with_retry(cursor, """
                            INSERT INTO cart (customer_id, store_id, item_id, quantity)
                            VALUES (%s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE 
                            quantity = LEAST(quantity + VALUES(quantity), %s),
                            created_at = NOW()
                        """, (session['user_id'], store_id, item['item_id'], available_quantity, item['stock_quantity']))
                        
                        added_to_cart.append({
                            'id': item['item_id'],
                            'name': item['item_name'],
                            'quantity': available_quantity,
                            'action': 'added_partial',
                            'note': f'Only {available_quantity} available (requested {item["quantity"]})'
                        })

            if not available_items and not added_to_cart:
                return jsonify({'error': 'None of the items from your previous order are available'}), 400

            response_data = {
                'success': True,
                'store_name': store_name,
                'added_to_cart': added_to_cart,
                'message': f'Successfully added {len(added_to_cart)} items to your cart'
            }
            
            if unavailable_items:
                response_data['unavailable_items'] = unavailable_items
                response_data['warning'] = f'{len(unavailable_items)} items were not fully added due to availability'

            return jsonify(response_data)

    except Exception as e:
        logger.error(f"Reorder error: {e}")
        return jsonify({'error': 'Server error'}), 500


@app.route('/campusmunchies.com/orders/<int:order_id>/received', methods=['POST'])
@login_required('customer')
def mark_order_received(order_id):
    """Mark order as received/delivered"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT id, status, order_number, customer_id
                FROM orders 
                WHERE id=%s AND customer_id=%s AND status='ready'
            """, (order_id, session['user_id']))
            
            order = cursor.fetchone()
            if not order:
                return jsonify({'error': 'Order not found or cannot be marked as received'}), 404

            execute_with_retry(cursor, """
                UPDATE orders 
                SET status='completed', delivered_at=NOW()
                WHERE id=%s
            """, (order_id,))

            create_notification(
                session['user_id'], 
                order_id, 
                'order_completed',
                f"Order {order['order_number']} has been marked as received."
            )

            try:
                execute_with_retry(cursor, """
                    SELECT c.email, c.phone, s.name as store_name
                    FROM customers c
                    JOIN orders o ON c.id = o.customer_id
                    JOIN stores s ON o.store_id = s.id
                    WHERE o.id=%s
                """, (order_id,))
                customer_data = cursor.fetchone()
                
                if customer_data:
                    send_order_status_update(
                        customer_data['email'],
                        customer_data['phone'],
                        order['order_number'],
                        'completed',
                        customer_data['store_name']
                    )
            except Exception as email_error:
                logger.error(f"Failed to send completion email: {email_error}")

            return jsonify({
                'success': True,
                'message': f"Order {order['order_number']} marked as received successfully"
            })

    except Exception as e:
        logger.error(f"Mark order received error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/api/order/<order_number>/status')
@login_required('customer')
def get_order_status(order_number):
    """Get order status for polling"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT status FROM orders 
                WHERE order_number=%s AND customer_id=%s
            """, (order_number, session['user_id']))
            
            order = cursor.fetchone()
            if not order:
                return jsonify({'error': 'Order not found'}), 404
                
            return jsonify({'status': order['status']})
            
    except Exception as e:
        logger.error(f"Get order status error: {e}")
        return jsonify({'error': 'Server error'}), 500
    
@app.route('/campusmunchies.com/orders/track/<int:order_id>')
@login_required('customer')
def track_order(order_id):
    """Get real-time order tracking information"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT o.id, o.order_number, o.status, o.created_at, o.estimated_time,
                       s.name as store_name, s.description as store_description
                FROM orders o
                JOIN stores s ON o.store_id = s.id
                WHERE o.id=%s AND o.customer_id=%s
            """, (order_id, session['user_id']))
            
            order = cursor.fetchone()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            # Get order timeline
            execute_with_retry(cursor, """
                SELECT status, created_at 
                FROM order_status_history 
                WHERE order_id=%s 
                ORDER BY created_at ASC
            """, (order_id,))
            timeline = cursor.fetchall()

            tracking_data = {
                'order_number': order['order_number'],
                'current_status': order['status'],
                'store_name': order['store_name'],
                'created_at': order['created_at'].isoformat() if order['created_at'] else None,
                'estimated_time': order['estimated_time'],
                'timeline': [
                    {
                        'status': item['status'],
                        'timestamp': item['created_at'].isoformat() if item['created_at'] else None
                    }
                    for item in timeline
                ]
            }

            return jsonify({'success': True, 'tracking': tracking_data})

    except Exception as e:
        logger.error(f"Track order error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/notifications')
@login_required('customer')
def notifications_page():
    """Notifications page - returns HTML"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT id, type, message, created_at, is_read, order_id
                FROM notifications
                WHERE customer_id=%s
                ORDER BY created_at DESC
            """, (session['user_id'],))
            notifications_data = cursor.fetchall()
            
        return render_template('notifications.html', notifications=notifications_data)
    except Exception as e:
        logger.error(f"Notifications error: {e}")
        return render_template('error.html', message='Server error loading notifications'), 500

@app.route('/campusmunchies.com/api/notifications')
@login_required('customer')
def api_notifications():
    """Get notifications as JSON for AJAX requests"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT id, type, message, created_at, is_read, order_id
                FROM notifications
                WHERE customer_id=%s
                ORDER BY created_at DESC
                LIMIT 50
            """, (session['user_id'],))
            notifications_data = cursor.fetchall()
            
            notifications = []
            for notif in notifications_data:
                notifications.append({
                    'id': notif['id'],
                    'type': notif['type'],
                    'message': notif['message'],
                    'created_at': notif['created_at'].isoformat() if notif['created_at'] else None,
                    'is_read': bool(notif['is_read']),
                    'order_id': notif['order_id']
                })
            
        return jsonify({'success': True, 'notifications': notifications})
    except Exception as e:
        logger.error(f"API notifications error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/orders/<int:order_id>/refund', methods=['POST'])
@login_required('customer')
def request_refund(order_id):
    """Request refund for an order"""
    data = request.get_json()
    reason = bleach.clean(data.get('reason', '')[:500])
    amount = float(data.get('amount', 0))  # 0 for full refund
    
    if not reason:
        return jsonify({'error': 'Reason required'}), 400
    
    try:
        # Verify order ownership
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT id, status, amount FROM orders 
                WHERE id=%s AND customer_id=%s
            """, (order_id, session['user_id']))
            order = cursor.fetchone()
            
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            if order['status'] not in ['paid', 'confirmed']:
                return jsonify({'error': 'Cannot refund this order'}), 400
        
        result = payment_service.process_refund(
            order_id, 
            amount if amount > 0 else None, 
            reason
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Refund request error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/notifications/count')
@login_required('customer')
def get_notification_count():
    """Get unread notification count"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT COUNT(*) as unread_count
                FROM notifications
                WHERE customer_id=%s AND is_read=FALSE
            """, (session['user_id'],))
            result = cursor.fetchone()
            
        return jsonify({'success': True, 'unread_count': result['unread_count']})
    except Exception as e:
        logger.error(f"Get notification count error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/notifications/clear', methods=['POST'])
@login_required('customer')
def clear_notifications():
    """Clear all notifications for current user"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                DELETE FROM notifications WHERE customer_id=%s
            """, (session['user_id'],))
            
        return jsonify({'success': True, 'message': 'All notifications cleared'})
    except Exception as e:
        logger.error(f"Clear notifications error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/notifications/<int:notification_id>/read', methods=['POST'])
@login_required('customer')
def mark_notification_read(notification_id):
    """Mark a specific notification as read"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                UPDATE notifications 
                SET is_read=TRUE 
                WHERE id=%s AND customer_id=%s
            """, (notification_id, session['user_id']))
            
            if cursor.rowcount == 0:
                return jsonify({'error': 'Notification not found'}), 404
                
        return jsonify({'success': True, 'message': 'Notification marked as read'})
    except Exception as e:
        logger.error(f"Mark notification read error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/notifications/read-all', methods=['POST'])
@login_required('customer')
def mark_all_notifications_read():
    """Mark all notifications as read for current user"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                UPDATE notifications 
                SET is_read=TRUE 
                WHERE customer_id=%s AND is_read=FALSE
            """, (session['user_id'],))
            
        return jsonify({'success': True, 'message': 'All notifications marked as read'})
    except Exception as e:
        logger.error(f"Mark all notifications read error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/notifications/<int:notification_id>/delete', methods=['POST'])
@login_required('customer')
def delete_notification(notification_id):
    """Delete a specific notification"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                DELETE FROM notifications 
                WHERE id=%s AND customer_id=%s
            """, (notification_id, session['user_id']))
            
            if cursor.rowcount == 0:
                return jsonify({'error': 'Notification not found'}), 404
                
        return jsonify({'success': True, 'message': 'Notification deleted'})
    except Exception as e:
        logger.error(f"Delete notification error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/payment/card/create-intent', methods=['POST'])
@login_required('customer')
def create_payment_intent():
    data = request.get_json()
    amount = data.get('amount')
    currency = data.get('currency', 'zar')
    
    if not amount or amount <= 0:
        return jsonify({'error': 'Invalid amount'}), 400

    try:
        import stripe
        if not app.config.get('STRIPE_SECRET_KEY'):
            return jsonify({'error': 'Stripe not configured'}), 500
            
        stripe.api_key = app.config['STRIPE_SECRET_KEY']
        
        payment_intent = stripe.PaymentIntent.create(
            amount=int(amount * 100),
            currency=currency,
            metadata={
                'customer_id': session['user_id'],
                'customer_username': session['username']
            }
        )
        
        return jsonify({
            'client_secret': payment_intent.client_secret,
            'payment_intent_id': payment_intent.id
        })
        
    except ImportError:
        logger.error("Stripe library not installed")
        return jsonify({'error': 'Payment processing not available'}), 500
    except Exception as e:
        logger.error(f"Stripe payment intent error: {e}")
        return jsonify({'error': 'Payment processing error'}), 500

@app.route('/campusmunchies.com/payment/card/success', methods=['POST'])
@login_required('customer')
def card_payment_success():
    data = request.get_json()
    payment_intent_id = data.get('payment_intent_id')
    
    if not payment_intent_id:
        return jsonify({'error': 'Payment intent ID required'}), 400
    
    try:
        import stripe
        stripe.api_key = app.config['STRIPE_SECRET_KEY']
        
        payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        if payment_intent.status == 'succeeded':
            with get_db_cursor() as cursor:
                execute_with_retry(cursor, """
                    UPDATE transactions SET status='completed', provider_data=JSON_SET(provider_data, '$.payment_intent', %s)
                    WHERE JSON_EXTRACT(provider_data, '$.payment_intent_id') = %s
                """, (json.dumps(payment_intent.to_dict()), payment_intent_id))
                
                execute_with_retry(cursor, """
                    UPDATE orders SET status='paid' 
                    WHERE id = (
                        SELECT order_id FROM transactions 
                        WHERE JSON_EXTRACT(provider_data, '$.payment_intent_id') = %s LIMIT 1
                    )
                """, (payment_intent_id,))
            
            create_notification(session['user_id'], None, 'payment_success', 'Card payment completed.')
            return jsonify({'success': True, 'message': 'Payment successful'})
        else:
            return jsonify({'error': 'Payment not completed'}), 400
            
    except Exception as e:
        logger.error(f"Card payment success error: {e}")
        return jsonify({'error': 'Payment processing error'}), 500

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    try:
        data = request.get_json()
        logger.info(f"M-Pesa callback: {json.dumps(data)}")
        if data and data.get('Body', {}).get('stkCallback', {}).get('ResultCode') == 0:
            result = data['Body']['stkCallback']['CallbackMetadata']['Item']
            amount = next(item['Value'] for item in result if item['Name'] == 'Amount')
            receipt = next(item['Value'] for item in result if item['Name'] == 'MpesaReceiptNumber')
            phone = next(item['Value'] for item in result if item['Name'] == 'PhoneNumber')
            
            with get_db_cursor() as cursor:
                execute_with_retry(cursor, """
                    UPDATE transactions 
                    SET status='completed', provider_data=JSON_SET(provider_data, '$.receipt', %s)
                    WHERE JSON_EXTRACT(provider_data, '$.CheckoutRequestID') = %s
                """, (receipt, data['Body']['stkCallback']['CheckoutRequestID']))
                
                execute_with_retry(cursor, """
                    UPDATE orders 
                    SET status='paid' 
                    WHERE id = (
                        SELECT order_id FROM transactions 
                        WHERE JSON_EXTRACT(provider_data, '$.CheckoutRequestID') = %s LIMIT 1
                    )
                """, (data['Body']['stkCallback']['CheckoutRequestID'],))
            
            logger.info(f"M-Pesa payment confirmed for receipt {receipt}")
        return jsonify({"ResultCode": 0, "ResultDesc": "Success"})
    except Exception as e:
        logger.error(f"M-Pesa callback error: {e}")
        return jsonify({"ResultCode": 1, "ResultDesc": "Failed"})

@app.route('/campusmunchies.com/order/success/<int:order_id>')
@login_required('customer')
def order_success(order_id):
    """Display success page for a specific order"""
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT o.*, s.name as store_name, c.username, c.phone as customer_phone
                FROM orders o
                JOIN stores s ON o.store_id = s.id
                JOIN customers c ON o.customer_id = c.id
                WHERE o.id=%s AND o.customer_id=%s
            """, (order_id, session['user_id']))
            
            order = cursor.fetchone()
            if not order:
                flash('Order not found', 'error')
                return redirect('/campusmunchies.com/orders')

            execute_with_retry(cursor, """
                SELECT mi.name, oi.quantity, oi.price
                FROM order_items oi
                JOIN menu_items mi ON oi.item_id = mi.id
                WHERE oi.order_id=%s
            """, (order_id,))
            items = cursor.fetchall()

        return render_template('successful.html', order=order, items=items)
        
    except Exception as e:
        logger.error(f"Error loading order success page: {e}")
        flash('Error loading order details', 'error')
        return redirect('/campusmunchies.com/orders')

@app.route('/campusmunchies.com/<store_name>/cart/validate', methods=['POST'])
@login_required('customer')
def validate_cart(store_name):
    data = request.get_json()
    cart_items = data.get('items', [])
    
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id FROM stores WHERE LOWER(name)=%s", (store_name.lower(),))
            store = cursor.fetchone()
            if not store:
                return jsonify({'error': 'Store not found'}), 404
            
            validated_items = []
            total = 0
            all_valid = True
            
            for item in cart_items:
                execute_with_retry(cursor, """
                    SELECT id, name, price, stock_quantity 
                    FROM menu_items 
                    WHERE id=%s AND store_id=%s AND availability=1
                """, (item['id'], store['id']))
                
                menu_item = cursor.fetchone()
                if not menu_item:
                    all_valid = False
                    continue
                
                quantity = min(item.get('quantity', 1), menu_item['stock_quantity'])
                if quantity > 0:
                    validated_items.append({
                        'id': menu_item['id'],
                        'name': menu_item['name'],
                        'price': float(menu_item['price']),
                        'quantity': quantity,
                        'max_quantity': menu_item['stock_quantity']
                    })
                    total += float(menu_item['price']) * quantity
                else:
                    all_valid = False
            
            return jsonify({
                'valid': all_valid and len(validated_items) == len(cart_items),
                'validated_items': validated_items,
                'total': total
            })
            
    except Exception as e:
        logger.error(f"Validate cart error: {e}")
        return jsonify({'error': 'Server error'}), 500

 # Admin Dashboard Routes

@app.route('/campusmunchies.com/admin/<store_name>', strict_slashes=False)
@login_required('admin')
def admin_dashboard(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
    
    try:
        return render_template('admin_dashboard.html', store_name=session.get('store_name'))
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/stats')
@login_required('admin')
def admin_stats(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            
            execute_with_retry(cursor, """
                SELECT COUNT(*) as today_orders, COALESCE(SUM(amount), 0) as today_revenue
                FROM orders 
                WHERE store_id=%s AND DATE(created_at) = CURDATE() AND status != 'cancelled'
            """, (store_id,))
            today_stats = cursor.fetchone()
            
            execute_with_retry(cursor, """
                SELECT COUNT(*) as pending_orders
                FROM orders 
                WHERE store_id=%s AND status IN ('pending', 'confirmed')
            """, (store_id,))
            pending_stats = cursor.fetchone()
            
            execute_with_retry(cursor, """
                SELECT COALESCE(SUM(amount), 0) as total_revenue
                FROM orders 
                WHERE store_id=%s AND status != 'cancelled'
            """, (store_id,))
            revenue_stats = cursor.fetchone()
            
            execute_with_retry(cursor, """
                SELECT COALESCE(AVG(rating), 0) as avg_rating
                FROM feedback 
                WHERE store_id=%s
            """, (store_id,))
            rating_stats = cursor.fetchone()
            
        return jsonify({
            'today_orders': today_stats['today_orders'],
            'today_revenue': float(today_stats['today_revenue']),
            'pending_orders': pending_stats['pending_orders'],
            'total_revenue': float(revenue_stats['total_revenue']),
            'avg_rating': float(rating_stats['avg_rating'])
        })
        
    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/orders')
@login_required('admin')
def admin_orders(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                SELECT 
                    o.id, o.order_number, c.username as customer_name, 
                    o.amount, o.status, o.created_at, o.order_type, 
                    o.delivery_address
                FROM orders o
                JOIN customers c ON o.customer_id = c.id
                WHERE o.store_id=%s
                ORDER BY o.created_at DESC
                LIMIT 20
            """, (store_id,))
            orders = cursor.fetchall()
            
        return jsonify(orders)
        
    except Exception as e:
        logger.error(f"Admin orders error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/orders/<int:order_id>')
@login_required('admin')
def admin_order_details(store_name, order_id):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            
            execute_with_retry(cursor, """
                SELECT 
                    o.id, o.order_number, o.amount, o.status, o.payment_method, 
                    o.order_type, o.delivery_address, o.created_at,
                    c.username as customer_name, c.email as customer_email, 
                    c.phone as customer_phone
                FROM orders o
                JOIN customers c ON o.customer_id = c.id
                WHERE o.id=%s AND o.store_id=%s
            """, (order_id, store_id))
            
            order = cursor.fetchone()
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            execute_with_retry(cursor, """
                SELECT mi.name, oi.quantity, oi.price
                FROM order_items oi
                JOIN menu_items mi ON oi.item_id = mi.id
                WHERE oi.order_id=%s
            """, (order_id,))
            
            items = cursor.fetchall()
            
            order_details = dict(order)
            order_details['items'] = items
            order_details['amount'] = float(order_details['amount'])
            
        return jsonify(order_details)
        
    except Exception as e:
        logger.error(f"Admin order details error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/orders/<int:order_id>/status', methods=['PATCH'])
@login_required('admin')
def admin_update_order_status(store_name, order_id):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    data = request.get_json()
    new_status = data.get('status')
    
    valid_statuses = ['pending', 'confirmed', 'ready', 'delivered', 'cancelled']
    if new_status not in valid_statuses:
        return jsonify({'error': 'Invalid status'}), 400
    
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            
            execute_with_retry(cursor, """
                SELECT customer_id, status FROM orders 
                WHERE id=%s AND store_id=%s
            """, (order_id, store_id))
            order = cursor.fetchone()
            
            if not order:
                return jsonify({'error': 'Order not found'}), 404
                
            if order['status'] in ['delivered', 'cancelled']:
                return jsonify({'error': 'Cannot update delivered or cancelled orders'}), 400
                
            execute_with_retry(cursor, """
                UPDATE orders SET status=%s WHERE id=%s
            """, (new_status, order_id))
            
            execute_with_retry(cursor, """
                INSERT INTO order_status_history (order_id, status)
                VALUES (%s, %s)
            """, (order_id, new_status))
        
        create_notification(order['customer_id'], order_id, 'order_update', f"Order status updated to {new_status}")
        
        return jsonify({'success': True, 'message': 'Order status updated'})
        
    except Exception as e:
        logger.error(f"Update order status error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/menu')
@login_required('admin')
def admin_menu_items(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                SELECT id, name, category, price, description, image_url, stock_quantity, availability, is_special
                FROM menu_items
                WHERE store_id=%s
                ORDER BY category, name
            """, (store_id,))
            menu_items = cursor.fetchall()
            
        return jsonify(menu_items)
        
    except Exception as e:
        logger.error(f"Admin menu items error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/menu', methods=['POST'])
@login_required('admin')
def admin_add_menu_item(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
    
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name = bleach.clean(data.get('name', '').strip()[:100])
    category = bleach.clean(data.get('category', '').strip()[:50])
    price = float(data.get('price', 0))
    description = bleach.clean(data.get('description', '').strip()[:500])
    image_url = bleach.clean(data.get('image_url', '').strip()[:200])
    stock_quantity = int(data.get('stock_quantity', 0))
    availability = bool(data.get('availability', True))
    is_special = bool(data.get('is_special', False))

    if not name or not category or price <= 0:
        return jsonify({'error': 'Name, category, and valid price required'}), 400

    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                INSERT INTO menu_items (store_id, name, category, price, description, image_url, stock_quantity, availability, is_special)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (store_id, name, category, price, description, image_url, stock_quantity, availability, is_special))
            
        logger.info(f"Menu item {name} added by admin for {store_name}")
        return jsonify({'success': True, 'message': 'Menu item added successfully'})
        
    except Exception as e:
        logger.error(f"Add menu item error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/menu/<int:item_id>', methods=['PUT'])
@login_required('admin')
def admin_update_menu_item(store_name, item_id):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
    
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name = bleach.clean(data.get('name', '').strip()[:100])
    category = bleach.clean(data.get('category', '').strip()[:50])
    price = float(data.get('price', 0))
    description = bleach.clean(data.get('description', '').strip()[:500])
    image_url = bleach.clean(data.get('image_url', '').strip()[:200])
    stock_quantity = int(data.get('stock_quantity', 0))
    availability = bool(data.get('availability', True))
    is_special = bool(data.get('is_special', False))

    if not name or not category or price <= 0:
        return jsonify({'error': 'Name, category, and valid price required'}), 400

    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                UPDATE menu_items 
                SET name=%s, category=%s, price=%s, description=%s, image_url=%s, 
                    stock_quantity=%s, availability=%s, is_special=%s, updated_at=NOW()
                WHERE id=%s AND store_id=%s
            """, (name, category, price, description, image_url, stock_quantity, availability, is_special, item_id, store_id))
            
            if cursor.rowcount == 0:
                return jsonify({'error': 'Item not found or access denied'}), 404
                
        logger.info(f"Menu item {item_id} updated by admin for {store_name}")
        return jsonify({'success': True, 'message': 'Menu item updated successfully'})
        
    except Exception as e:
        logger.error(f"Update menu item error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/menu/<int:item_id>', methods=['DELETE'])
@login_required('admin')
def admin_delete_menu_item(store_name, item_id):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403

    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                DELETE FROM menu_items 
                WHERE id=%s AND store_id=%s
            """, (item_id, store_id))
            
            if cursor.rowcount == 0:
                return jsonify({'error': 'Item not found'}), 404
                
        logger.info(f"Menu item {item_id} deleted by admin for {store_name}")
        return jsonify({'success': True, 'message': 'Menu item deleted successfully'})
        
    except Exception as e:
        logger.error(f"Delete menu item error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/menu/<int:item_id>/availability', methods=['PATCH'])
@login_required('admin')
def admin_toggle_availability(store_name, item_id):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
    
    data = request.get_json()
    availability = bool(data.get('availability', True))

    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                UPDATE menu_items 
                SET availability=%s, updated_at=NOW()
                WHERE id=%s AND store_id=%s
            """, (availability, item_id, store_id))
            
            if cursor.rowcount == 0:
                return jsonify({'error': 'Item not found'}), 404
                
        logger.info(f"Menu item {item_id} availability set to {availability} by admin for {store_name}")
        return jsonify({'success': True, 'message': f'Menu item {"enabled" if availability else "disabled"}'})
        
    except Exception as e:
        logger.error(f"Toggle availability error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/feedback')
@login_required('admin')
def admin_feedback(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            execute_with_retry(cursor, """
                SELECT f.id, f.rating, f.comment, f.response, c.username as customer_name, f.created_at
                FROM feedback f
                JOIN customers c ON f.customer_id = c.id
                WHERE f.store_id=%s
                ORDER BY f.created_at DESC
                LIMIT 10
            """, (store_id,))
            feedback = cursor.fetchall()
            
        return jsonify(feedback)
        
    except Exception as e:
        logger.error(f"Admin feedback error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/feedback/<int:feedback_id>/response', methods=['POST'])
@login_required('admin')
def admin_respond_to_feedback(store_name, feedback_id):
    # Verify store access
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403

    # Get JSON payload
    data = request.get_json()
    response_text = bleach.clean(data.get('response', '').strip()[:500])

    if not response_text:
        return jsonify({'error': 'Response is required'}), 400

    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')

            # Check if the column exists before updating
            cursor.execute("""
                SHOW COLUMNS FROM feedback LIKE 'responded_at'
            """)
            has_responded_at = cursor.fetchone() is not None

            if has_responded_at:
                sql = """
                    UPDATE feedback
                    SET response=%s, responded_at=NOW()
                    WHERE id=%s AND store_id=%s
                """
                params = (response_text, feedback_id, store_id)
            else:
                sql = """
                    UPDATE feedback
                    SET response=%s
                    WHERE id=%s AND store_id=%s
                """
                params = (response_text, feedback_id, store_id)

            execute_with_retry(cursor, sql, params)

            if cursor.rowcount == 0:
                return jsonify({'error': 'Feedback not found'}), 404

            # Commit the change
            cursor.connection.commit()

        logger.info(f"Response added to feedback {feedback_id} by admin for {store_name}")
        return jsonify({'success': True, 'message': 'Response sent successfully'})

    except Exception as e:
        logger.error(f"Respond to feedback error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/admin/<store_name>/reports')
@login_required('admin')
def admin_reports(store_name):
    if session.get('store_name').lower() != store_name.lower():
        return jsonify({"error": "Access denied"}), 403
        
    try:
        with get_db_cursor() as cursor:
            store_id = session.get('store_id')
            
            execute_with_retry(cursor, """
                SELECT 
                    DATE(created_at) as date,
                    COUNT(*) as order_count,
                    COALESCE(SUM(amount), 0) as daily_revenue,
                    AVG(amount) as avg_order_value
                FROM orders 
                WHERE store_id=%s 
                AND status != 'cancelled'
                AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            """, (store_id,))
            sales_data = cursor.fetchall()
            
            execute_with_retry(cursor, """
                SELECT 
                    mi.name,
                    SUM(oi.quantity) as total_sold,
                    SUM(oi.quantity * oi.price) as total_revenue
                FROM order_items oi
                JOIN menu_items mi ON oi.item_id = mi.id
                JOIN orders o ON oi.order_id = o.id
                WHERE o.store_id=%s 
                AND o.status != 'cancelled'
                AND o.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY mi.id, mi.name
                ORDER BY total_sold DESC
                LIMIT 10
            """, (store_id,))
            top_items = cursor.fetchall()
            
            execute_with_retry(cursor, """
                SELECT 
                    order_type,
                    COUNT(*) as order_count,
                    COALESCE(SUM(amount), 0) as revenue
                FROM orders 
                WHERE store_id=%s 
                AND status != 'cancelled'
                AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY order_type
            """, (store_id,))
            order_types = cursor.fetchall()
            
            execute_with_retry(cursor, """
                SELECT 
                    payment_method,
                    COUNT(*) as order_count,
                    COALESCE(SUM(amount), 0) as revenue
                FROM orders 
                WHERE store_id=%s 
                AND status != 'cancelled'
                AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY payment_method
            """, (store_id,))
            payment_methods = cursor.fetchall()
            
        report_data = {
            'sales_data': [
                {
                    'date': item['date'].isoformat() if item['date'] else None,
                    'order_count': item['order_count'],
                    'daily_revenue': float(item['daily_revenue']),
                    'avg_order_value': float(item['avg_order_value'])
                }
                for item in sales_data
            ],
            'top_items': [
                {
                    'name': item['name'],
                    'total_sold': item['total_sold'],
                    'total_revenue': float(item['total_revenue'])
                }
                for item in top_items
            ],
            'order_types': [
                {
                    'order_type': item['order_type'],
                    'order_count': item['order_count'],
                    'revenue': float(item['revenue'])
                }
                for item in order_types
            ],
            'payment_methods': [
                {
                    'payment_method': item['payment_method'],
                    'order_count': item['order_count'],
                    'revenue': float(item['revenue'])
                }
                for item in payment_methods
            ]
        }
        
        return jsonify(report_data)
        
    except Exception as e:
        logger.error(f"Admin reports error: {e}")
        return jsonify({'error': 'Server error'}), 500
    
@app.route('/campusmunchies.com/superadmin/')
@login_required('superadmin')
def superadmin_dashboard():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id, name, description FROM stores")
            stores = cursor.fetchall()
        return render_template('superadmin.html', stores=stores)
    except Exception as e:
        logger.error(f"Superadmin dashboard error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/stores/add', methods=['POST'])
@login_required('superadmin')
def superadmin_add_store():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    try:
        name = bleach.clean(data.get('name', '').strip()[:100])
        description = bleach.clean(data.get('description', '').strip()[:500])
        location = bleach.clean(data.get('location', '').strip()[:200])
        contact_email = bleach.clean(data.get('contact_email', '').strip()[:100])
        contact_phone = bleach.clean(data.get('contact_phone', '').strip()[:20])
        opening_hours = bleach.clean(data.get('opening_hours', '').strip()[:50])
        is_active = data.get('is_active', True)
        
        if not name or not description:
            return jsonify({'error': 'Name and description required'}), 400
            
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                INSERT INTO stores (name, description, location, contact_email, contact_phone, opening_hours, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (name, description, location, contact_email, contact_phone, opening_hours, is_active))
            
        return jsonify({'success': True, 'message': 'Store added successfully'})
        
    except MySQLdb.IntegrityError as e:
        return jsonify({'error': 'Store name already exists'}), 400
    except Exception as e:
        logger.error(f"Add store error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/stores/<int:store_id>/edit', methods=['POST'])
@login_required('superadmin')
def superadmin_edit_store(store_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    try:
        name = bleach.clean(data.get('name', '').strip()[:100])
        description = bleach.clean(data.get('description', '').strip()[:500])
        location = bleach.clean(data.get('location', '').strip()[:200])
        contact_email = bleach.clean(data.get('contact_email', '').strip()[:100])
        contact_phone = bleach.clean(data.get('contact_phone', '').strip()[:20])
        opening_hours = bleach.clean(data.get('opening_hours', '').strip()[:50])
        is_active = data.get('is_active', True)
        
        if not name or not description:
            return jsonify({'error': 'Name and description required'}), 400
            
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                UPDATE stores 
                SET name=%s, description=%s, location=%s, contact_email=%s, 
                    contact_phone=%s, opening_hours=%s, is_active=%s
                WHERE id=%s
            """, (name, description, location, contact_email, contact_phone, opening_hours, is_active, store_id))
            
        return jsonify({'success': True, 'message': 'Store updated successfully'})
        
    except MySQLdb.IntegrityError as e:
        return jsonify({'error': 'Store name already exists'}), 400
    except Exception as e:
        logger.error(f"Edit store error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/stores/<int:store_id>/delete', methods=['POST'])
@login_required('superadmin')
def superadmin_delete_store(store_id):
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "DELETE FROM stores WHERE id=%s", (store_id,))
        return jsonify({'success': True, 'message': 'Store deleted successfully'})
    except Exception as e:
        logger.error(f"Delete store error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/stores/<int:store_id>/status', methods=['POST'])
@login_required('superadmin')
def superadmin_toggle_store_status_id(store_id):
    data = request.get_json()
    is_active = data.get('is_active', True)
    
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, 
                "UPDATE stores SET is_active=%s WHERE id=%s", 
                (is_active, store_id))
        return jsonify({'success': True, 'message': f'Store {"activated" if is_active else "deactivated"}'})
    except Exception as e:
        logger.error(f"Toggle store status error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/admins/add', methods=['POST'])
@login_required('superadmin')
def superadmin_add_admin():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    try:
        store_id = data.get('store_id')
        username = bleach.clean(data.get('username', '').strip()[:50])
        email = bleach.clean(data.get('email', '').strip()[:100])
        password = data.get('password', '')[:100]
        role = bleach.clean(data.get('role', 'admin').strip()[:20])
        is_active = data.get('is_active', True)
        
        if not store_id or not username or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
            
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        with get_db_cursor() as cursor:
            # Check if username or email already exists
            execute_with_retry(cursor, 
                "SELECT id FROM admins WHERE username=%s OR email=%s", 
                (username, email))
            if cursor.fetchone():
                return jsonify({'error': 'Username or email already exists'}), 400
            
            password_hash = hash_password(password)
            execute_with_retry(cursor, """
                INSERT INTO admins (store_id, username, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (store_id, username, email, password_hash, role, is_active))
            
        return jsonify({'success': True, 'message': 'Admin created successfully'})
    
    except MySQLdb.IntegrityError as e:
        return jsonify({'error': 'Username or email already exists'}), 400
    except Exception as e:
        logger.error(f"Add admin error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/admins/<int:admin_id>/edit', methods=['POST'])
@login_required('superadmin')
def superadmin_edit_admin(admin_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    try:
        store_id = data.get('store_id')
        username = bleach.clean(data.get('username', '').strip()[:50])
        email = bleach.clean(data.get('email', '').strip()[:100])
        role = bleach.clean(data.get('role', 'admin').strip()[:20])
        is_active = data.get('is_active', True)
        password = data.get('password')
        
        if not store_id or not username or not email:
            return jsonify({'error': 'All fields are required'}), 400
        
        with get_db_cursor() as cursor:
            # Check if username or email already exists for other admins
            execute_with_retry(cursor, 
                "SELECT id FROM admins WHERE (username=%s OR email=%s) AND id != %s", 
                (username, email, admin_id))
            if cursor.fetchone():
                return jsonify({'error': 'Username or email already exists'}), 400
            
            if password:
                if len(password) < 6:
                    return jsonify({'error': 'Password must be at least 6 characters'}), 400
                password_hash = hash_password(password)
                execute_with_retry(cursor, """
                    UPDATE admins 
                    SET store_id=%s, username=%s, email=%s, password_hash=%s, role=%s, is_active=%s
                    WHERE id=%s
                """, (store_id, username, email, password_hash, role, is_active, admin_id))
            else:
                execute_with_retry(cursor, """
                    UPDATE admins 
                    SET store_id=%s, username=%s, email=%s, role=%s, is_active=%s
                    WHERE id=%s
                """, (store_id, username, email, role, is_active, admin_id))
                
        return jsonify({'success': True, 'message': 'Admin updated successfully'})
    
    except MySQLdb.IntegrityError as e:
        return jsonify({'error': 'Username or email already exists'}), 400
    except Exception as e:
        logger.error(f"Edit admin error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/admins/<int:admin_id>/delete', methods=['POST'])
@login_required('superadmin')
def superadmin_delete_admin(admin_id):
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "DELETE FROM admins WHERE id=%s", (admin_id,))
        return jsonify({'success': True, 'message': 'Admin deleted successfully'})
    except Exception as e:
        logger.error(f"Delete admin error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/stats')
@login_required('superadmin')
def superadmin_stats():
    try:
        with get_db_cursor() as cursor:
            # Total stores and active stores
            execute_with_retry(cursor, "SELECT COUNT(*) as total_stores, SUM(is_active) as active_stores FROM stores")
            stores_stats = cursor.fetchone()
            
            # Total admins and active admins
            execute_with_retry(cursor, "SELECT COUNT(*) as total_admins, SUM(is_active) as active_admins FROM admins")
            admins_stats = cursor.fetchone()
            
            # Total customers
            execute_with_retry(cursor, "SELECT COUNT(*) as total_customers FROM customers")
            customers_stats = cursor.fetchone()
            
            # Platform revenue and orders
            execute_with_retry(cursor, """
                SELECT 
                    COUNT(*) as total_orders,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_orders,
                    COALESCE(SUM(amount), 0) as platform_revenue
                FROM orders 
                WHERE status != 'cancelled'
            """)
            orders_stats = cursor.fetchone()
            
        return jsonify({
            'total_stores': stores_stats['total_stores'] or 0,
            'active_stores': stores_stats['active_stores'] or 0,
            'total_admins': admins_stats['total_admins'] or 0,
            'active_admins': admins_stats['active_admins'] or 0,
            'total_customers': customers_stats['total_customers'] or 0,
            'total_orders': orders_stats['total_orders'] or 0,
            'completed_orders': orders_stats['completed_orders'] or 0,
            'platform_revenue': float(orders_stats['platform_revenue'] or 0)
        })
        
    except Exception as e:
        logger.error(f"Superadmin stats error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/stores')
@login_required('superadmin')
def superadmin_stores():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT s.*, 
                       COUNT(DISTINCT o.id) as total_orders,
                       COALESCE(SUM(o.amount), 0) as total_revenue,
                       COUNT(DISTINCT f.id) as total_feedback
                FROM stores s
                LEFT JOIN orders o ON s.id = o.store_id AND o.status != 'cancelled'
                LEFT JOIN feedback f ON s.id = f.store_id
                GROUP BY s.id, s.name, s.description, s.location, s.contact_email, 
                         s.contact_phone, s.opening_hours, s.is_active, s.created_at
                ORDER BY s.name
            """)
            stores = cursor.fetchall()
            
        return jsonify(stores)
        
    except Exception as e:
        logger.error(f"Superadmin stores error: {e}")
        return jsonify({'error': 'Server error'}), 500
        
@app.route('/campusmunchies.com/superadmin/stores', methods=['POST'])
@login_required('superadmin')
def superadmin_manage_stores():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    action = data.get('action')
    
    try:
        with get_db_cursor() as cursor:
            if action == 'add':
                name = bleach.clean(data.get('name', '').strip()[:100])
                description = bleach.clean(data.get('description', '').strip()[:500])
                
                if not name or not description:
                    return jsonify({'error': 'Name and description required'}), 400
                    
                execute_with_retry(cursor, 
                    "INSERT INTO stores (name, description) VALUES (%s, %s)", 
                    (name, description))
                    
                return jsonify({'success': True, 'message': 'Store added successfully'})
            
            elif action == 'edit':
                store_id = data.get('store_id')
                name = bleach.clean(data.get('name', '').strip()[:100])
                description = bleach.clean(data.get('description', '').strip()[:500])
                
                if not store_id or not name or not description:
                    return jsonify({'error': 'Store ID, name, and description required'}), 400
                    
                execute_with_retry(cursor, 
                    "UPDATE stores SET name=%s, description=%s WHERE id=%s", 
                    (name, description, store_id))
                    
                return jsonify({'success': True, 'message': 'Store updated successfully'})
            
            elif action == 'delete':
                store_id = data.get('store_id')
                if not store_id:
                    return jsonify({'error': 'Store ID required'}), 400
                    
                execute_with_retry(cursor, "DELETE FROM stores WHERE id=%s", (store_id,))
                return jsonify({'success': True, 'message': 'Store deleted successfully'})
            
            else:
                return jsonify({'error': 'Invalid action'}), 400
    
    except MySQLdb.IntegrityError as e:
        return jsonify({'error': 'Store name already exists'}), 400
    except Exception as e:
        logger.error(f"Manage stores error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/admins')
@login_required('superadmin')
def superadmin_admins():
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, """
                SELECT a.*, s.name as store_name
                FROM admins a
                JOIN stores s ON a.store_id = s.id
                ORDER BY s.name, a.username
            """)
            admins = cursor.fetchall()
            
        return jsonify(admins)
        
    except Exception as e:
        logger.error(f"Superadmin admins error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/admins', methods=['POST'])
@login_required('superadmin')
def superadmin_manage_admins():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    action = data.get('action')
    
    try:
        with get_db_cursor() as cursor:
            if action == 'add':
                store_id = data.get('store_id')
                username = bleach.clean(data.get('username', '').strip()[:50])
                email = bleach.clean(data.get('email', '').strip()[:100])
                password = data.get('password', '')[:100]
                
                if not store_id or not username or not email or not password:
                    return jsonify({'error': 'All fields are required'}), 400
                    
                if len(password) < 6:
                    return jsonify({'error': 'Password must be at least 6 characters'}), 400
                
                # Check if username or email already exists
                execute_with_retry(cursor, 
                    "SELECT id FROM admins WHERE username=%s OR email=%s", 
                    (username, email))
                if cursor.fetchone():
                    return jsonify({'error': 'Username or email already exists'}), 400
                
                password_hash = hash_password(password)
                execute_with_retry(cursor, """
                    INSERT INTO admins (store_id, username, email, password_hash)
                    VALUES (%s, %s, %s, %s)
                """, (store_id, username, email, password_hash))
                
                return jsonify({'success': True, 'message': 'Admin created successfully'})
            
            elif action == 'delete':
                admin_id = data.get('admin_id')
                if not admin_id:
                    return jsonify({'error': 'Admin ID required'}), 400
                    
                execute_with_retry(cursor, "DELETE FROM admins WHERE id=%s", (admin_id,))
                return jsonify({'success': True, 'message': 'Admin deleted successfully'})
            
            else:
                return jsonify({'error': 'Invalid action'}), 400
    
    except MySQLdb.IntegrityError as e:
        return jsonify({'error': 'Username or email already exists'}), 400
    except Exception as e:
        logger.error(f"Manage admins error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/admins/reset-password', methods=['POST'])
@login_required('superadmin')
def superadmin_reset_admin_password():
    data = request.get_json()
    admin_id = data.get('admin_id')
    new_password = data.get('new_password', '')[:100]
    
    if not admin_id or not new_password:
        return jsonify({'error': 'Admin ID and new password required'}), 400
        
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
    try:
        with get_db_cursor() as cursor:
            password_hash = hash_password(new_password)
            execute_with_retry(cursor, 
                "UPDATE admins SET password_hash=%s WHERE id=%s", 
                (password_hash, admin_id))
                
            return jsonify({'success': True, 'message': 'Password reset successfully'})
            
    except Exception as e:
        logger.error(f"Reset admin password error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/reports/sales')
@login_required('superadmin')
def superadmin_sales_report():
    try:
        with get_db_cursor() as cursor:
            # Total revenue and orders
            execute_with_retry(cursor, """
                SELECT 
                    COUNT(*) as total_orders,
                    COALESCE(SUM(amount), 0) as total_revenue,
                    COALESCE(AVG(amount), 0) as avg_order_value
                FROM orders 
                WHERE status != 'cancelled'
            """)
            sales_stats = cursor.fetchone()
            
            # Top performing stores
            execute_with_retry(cursor, """
                SELECT s.name, COALESCE(SUM(o.amount), 0) as revenue
                FROM stores s
                LEFT JOIN orders o ON s.id = o.store_id AND o.status != 'cancelled'
                GROUP BY s.id, s.name
                ORDER BY revenue DESC
                LIMIT 5
            """)
            top_stores = cursor.fetchall()
            
        return jsonify({
            'total_orders': sales_stats['total_orders'],
            'total_revenue': float(sales_stats['total_revenue']),
            'avg_order_value': float(sales_stats['avg_order_value']),
            'top_stores': top_stores
        })
        
    except Exception as e:
        logger.error(f"Sales report error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/superadmin/reports/users')
@login_required('superadmin')
def superadmin_user_report():
    try:
        with get_db_cursor() as cursor:
            # Total customers
            execute_with_retry(cursor, "SELECT COUNT(*) as total_customers FROM customers")
            total_customers = cursor.fetchone()
            
            # New customers today
            execute_with_retry(cursor, """
                SELECT COUNT(*) as new_customers_today 
                FROM customers 
                WHERE DATE(created_at) = CURDATE()
            """)
            new_today = cursor.fetchone()
            
            # Active customers (placed at least one order)
            execute_with_retry(cursor, """
                SELECT COUNT(DISTINCT customer_id) as active_customers
                FROM orders
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
            """)
            active_customers = cursor.fetchone()
            
        return jsonify({
            'total_customers': total_customers['total_customers'],
            'new_customers_today': new_today['new_customers_today'],
            'active_customers': active_customers['active_customers']
        })
        
    except Exception as e:
        logger.error(f"User report error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/<store_name>/feedback')
@login_required('customer')
def feedback(store_name):
    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id, name FROM stores WHERE LOWER(name)=%s", (store_name.lower(),))
            store = cursor.fetchone()
            
        if not store:
            return "Store not found", 404
            
        return render_template('feedback.html', store=store)
    except Exception as e:
        logger.error(f"Feedback page error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/<store_name>/feedback/submit', methods=['POST'])
@login_required('customer')
def submit_feedback(store_name):
    data = request.get_json()
    rating = data.get('rating')
    comment = bleach.clean(data.get('comment', '').strip()[:1000])

    if not rating or not 1 <= int(rating) <= 5:
        return jsonify({'error': 'Valid rating required'}), 400

    try:
        with get_db_cursor() as cursor:
            execute_with_retry(cursor, "SELECT id FROM stores WHERE LOWER(name)=%s", (store_name.lower(),))
            store = cursor.fetchone()
            if not store:
                return jsonify({'error': 'Store not found'}), 404

            execute_with_retry(cursor, """
                INSERT INTO feedback (store_id, customer_id, rating, comment)
                VALUES (%s, %s, %s, %s)
            """, (store['id'], session['user_id'], int(rating), comment))
            
            execute_with_retry(cursor, """
                UPDATE stores 
                SET avg_rating = (
                    SELECT AVG(rating) FROM feedback WHERE store_id=%s
                )
                WHERE id=%s
            """, (store['id'], store['id']))

        return jsonify({'success': True, 'message': 'Thank you for your feedback!'})

    except Exception as e:
        logger.error(f"Submit feedback error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/campusmunchies.com/logout')
def logout():
    session.clear()
    return redirect('/campusmunchies.com/login')

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Server Error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(413)
def too_large(error):
    return jsonify({'error': 'File too large'}), 413

if __name__ == "__main__":
    app.run(debug=app.config['DEBUG'], host='0.0.0.0', port=5000)