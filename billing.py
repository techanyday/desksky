import os
from datetime import datetime, timedelta
from paystack.resource import TransactionResource, PlanResource
from paystack.utils import initialize_transaction

class PaystackBilling:
    def __init__(self):
        self.secret_key = os.getenv('PAYSTACK_SECRET_KEY')
        if not self.secret_key:
            raise ValueError("PAYSTACK_SECRET_KEY environment variable is not set")
        self.transaction = TransactionResource(self.secret_key)
        self.plan = PlanResource(self.secret_key)

    def create_one_time_payment(self, email, num_slides):
        """Create a one-time payment for slides"""
        if not email:
            raise ValueError("Email is required")
        if not num_slides or num_slides <= 0:
            raise ValueError("Number of slides must be greater than 0")
            
        amount = num_slides * 20  # $0.20 per slide = 20 cents
        try:
            reference = f"slides_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
            response = initialize_transaction(
                reference=reference,
                amount=amount * 100,  # Amount in kobo/cents
                email=email,
                plan=None
            )
            
            if not response.get('status'):
                raise ValueError(response.get('message', 'Payment initialization failed'))
                
            return response
            
        except Exception as e:
            return {'status': False, 'message': str(e)}

    def create_subscription(self, email):
        """Create a monthly subscription payment"""
        if not email:
            raise ValueError("Email is required")
            
        try:
            # Create or get existing plan
            plan_code = None
            plan_name = "Monthly Unlimited"
            amount = 299 * 100  # $2.99 in cents
            
            # Try to find existing plan
            plans = self.plan.list()
            for plan in plans.get('data', []):
                if plan.get('name') == plan_name and plan.get('amount') == amount:
                    plan_code = plan.get('plan_code')
                    break
            
            # Create plan if it doesn't exist
            if not plan_code:
                plan_response = self.plan.create(
                    name=plan_name,
                    amount=amount,
                    interval="monthly",
                    description="Unlimited slides generation"
                )
                
                if not plan_response.get('status'):
                    raise ValueError(plan_response.get('message', 'Failed to create plan'))
                    
                plan_code = plan_response.get('data', {}).get('plan_code')
            
            # Initialize transaction with plan
            reference = f"sub_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
            response = initialize_transaction(
                reference=reference,
                amount=amount,
                email=email,
                plan=plan_code
            )
            
            if not response.get('status'):
                raise ValueError(response.get('message', 'Payment initialization failed'))
                
            return response
            
        except Exception as e:
            return {'status': False, 'message': str(e)}

    def verify_payment(self, reference):
        """Verify a payment transaction"""
        if not reference:
            raise ValueError("Reference is required")
            
        try:
            response = self.transaction.verify(reference)
            
            if not response.get('status'):
                raise ValueError(response.get('message', 'Payment verification failed'))
                
            return response
            
        except Exception as e:
            return {'status': False, 'message': str(e)}

    def calculate_subscription_end(self):
        """Calculate subscription end date"""
        return datetime.now() + timedelta(days=30)

def update_user_credits(user, payment_type, num_slides=None):
    """Update user credits based on payment type"""
    if not user:
        raise ValueError("User is required")
    if not payment_type:
        raise ValueError("Payment type is required")
        
    try:
        if payment_type == 'one_time':
            if not num_slides or num_slides <= 0:
                raise ValueError("Number of slides must be greater than 0 for one-time payment")
            user.free_credits += num_slides
        elif payment_type == 'subscription':
            user.subscription_status = 'premium'
            user.subscription_end = PaystackBilling().calculate_subscription_end()
        else:
            raise ValueError(f"Invalid payment type: {payment_type}")
            
        return user
        
    except Exception as e:
        raise ValueError(f"Error updating user credits: {str(e)}")
