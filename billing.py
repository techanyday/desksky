import os
from datetime import datetime, timedelta
from paystack.resource import TransactionResource, PlanResource
from paystack.utils import initialize_transaction

class PaystackBilling:
    def __init__(self):
        self.secret_key = os.getenv('PAYSTACK_SECRET_KEY')
        self.transaction = TransactionResource(self.secret_key)
        self.plan = PlanResource(self.secret_key)

    def create_one_time_payment(self, email, num_slides):
        """Create a one-time payment for slides"""
        amount = num_slides * 20  # $0.20 per slide = 20 cents
        try:
            response = initialize_transaction(
                reference=f"slides_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                amount=amount * 100,  # Amount in kobo/cents
                email=email,
                plan=None
            )
            return response
        except Exception as e:
            return {'status': False, 'message': str(e)}

    def create_subscription(self, email):
        """Create a monthly subscription payment"""
        try:
            # Create plan if it doesn't exist
            plan_response = self.plan.create(
                name="Monthly Unlimited",
                amount=299 * 100,  # $2.99 in cents
                interval="monthly"
            )
            
            response = initialize_transaction(
                reference=f"sub_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                amount=299 * 100,
                email=email,
                plan=plan_response.get('data', {}).get('plan_code')
            )
            return response
        except Exception as e:
            return {'status': False, 'message': str(e)}

    def verify_payment(self, reference):
        """Verify a payment transaction"""
        try:
            response = self.transaction.verify(reference)
            return response
        except Exception as e:
            return {'status': False, 'message': str(e)}

    def calculate_subscription_end(self):
        """Calculate subscription end date"""
        return datetime.now() + timedelta(days=30)

def update_user_credits(user, payment_type, num_slides=None):
    """Update user credits based on payment type"""
    if payment_type == 'one_time':
        user.free_credits += num_slides
    elif payment_type == 'subscription':
        user.subscription_status = 'premium'
        user.subscription_end = PaystackBilling().calculate_subscription_end()
    return user
