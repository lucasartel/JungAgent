import stripe
import os
import logging
from dotenv import load_dotenv

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# As variaveis de ambiente de preco devem ser IDs de produtos no Stripe (ex: price_12345)
STRIPE_PRICE_BASIC_7_DAYS = os.getenv("STRIPE_PRICE_BASIC_7_DAYS", "price_1xxxxxx_basic")
STRIPE_PRICE_PREMIUM_COMPANION = os.getenv("STRIPE_PRICE_PREMIUM_COMPANION", "price_1xxxxxx_premium")

# URL de sucesso customizada para o bot do telegram
SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://t.me/JungClaudeBot")

logger = logging.getLogger(__name__)

def create_checkout_session(telegram_user_id: str, plan_type: str) -> str:
    """
    Cria uma sessão de checkout no Stripe para o usuário comprar um plano.
    
    plan_type: 'basic_7_days' ou 'premium_companion'
    Retorna a URL de pagamento do Stripe.
    """
    try:
        if plan_type == 'basic_7_days':
            price_id = STRIPE_PRICE_BASIC_7_DAYS
            mode = 'payment' # ou 'subscription' dependendo de como voce criar no Stripe, mas 7 dias fixo geralmente é payment unico
        elif plan_type == 'premium_companion':
            price_id = STRIPE_PRICE_PREMIUM_COMPANION
            mode = 'subscription'
        else:
            raise ValueError(f"Plano inválido: {plan_type}")

        # Passar o telegram_user_id como metadata para sabermos quem pagou no Webhook
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card', 'pix'],
            line_items=[
                {
                    'price': price_id,
                    'quantity': 1,
                },
            ],
            mode=mode,
            success_url=SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=SUCCESS_URL,
            client_reference_id=telegram_user_id,
            metadata={
                "telegram_user_id": telegram_user_id,
                "plan_type": plan_type
            }
        )

        return checkout_session.url

    except Exception as e:
        logger.error(f"Erro ao criar sessão de checkout Stripe: {e}", exc_info=True)
        return None
