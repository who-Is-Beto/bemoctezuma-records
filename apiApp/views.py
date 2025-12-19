from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from django.db.models import Q
from .models import Record, Category, Cart, CartItem, Wishlist, WishlistItem, Review, Order, OrderItem, Artist
from .serilizers import (
    RecordDetailSerializer,
    RecordListSerializer,
    CategorySerializer,
    CategoryListSerializer,
    CartSerializer,
    CartItemSerializer,
    WishlistSerializer,
    ReviewSerializer,
    ArtistSerializer,
)
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from .pagination import StandardResultsSetPagination
import stripe

User = get_user_model()
stripe.api_key = settings.STRIPE_SECRET_KEY
endpoint_secret = settings.WEBHOOK_SECRET

@api_view(['GET'])
def record_list(request):
    records = Record.objects.filter(featured=True)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(records, request)
    serializer = RecordListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

@api_view(['GET'])
def artist_list(request):
    artists = Artist.objects.all().order_by('name')
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(artists, request)
    serializer = ArtistSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

@api_view(['GET'])
def record_detail(_, slug):
    try:
        record = Record.objects.get(slug=slug)
    except Record.DoesNotExist:  
        return Response({"error": "Product not found"}, status=404)
    
    serializer = RecordDetailSerializer(record)
    return Response(serializer.data)

@api_view(['GET'])
def get_category_list(_):
    categories = Category.objects.all()
    serializer = CategoryListSerializer(categories, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_category_detail(_, slug):
    try:
        category = Category.objects.get(slug=slug)
    except Category.DoesNotExist:
        return Response({"error": "Category not found"}, status=404)
    
    serializer = CategorySerializer(category)
    return Response(serializer.data)

@api_view(['GET'])
def get_cart(_, cart_code):
    try:
        cart = Cart.objects.get(cart_code=cart_code)
    except Cart.DoesNotExist:
        return Response({"error": "Cart not found"}, status=404)
    
    serializer = CartSerializer(cart)
    return Response(serializer.data)

@api_view(['GET'])
def get_all_carts(_):
    carts = Cart.objects.all()
    serializer = CartSerializer(carts, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_all_cart_items(_):
    cart_items = CartItem.objects.all()
    serializer = CartItemSerializer(cart_items, many=True)
    return Response(serializer.data)

@api_view(['POST'])
def add_to_cart(request):
    cart_code = request.data.get('cart_code')
    record_id = int(request.data.get('record_id'))
    email = request.data.get('email')
    quantity = int(request.data.get('quantity', 1))
    user = User.objects.get(email=email) if email else None
    if cart_code:
        cart, _ = Cart.objects.get_or_create(cart_code=cart_code, defaults={'user': user})
    else:
        cart = Cart.objects.create(user=user)
    record = Record.objects.get(id=str(record_id))
    cart_item, created = CartItem.objects.get_or_create(cart=cart, record=record)
    if not created:
        cart_item.quantity += quantity
    else:
        cart_item.quantity = quantity
    cart_item.save()

    serializer = CartSerializer(cart)
    return Response(serializer.data)

@api_view(['PUT'])
def update_cart_quantity(request):
    cart_item_id = request.data.get('item_id')
    quantity = int(request.data.get('quantity'))

    cartitem = CartItem.objects.get(id=cart_item_id)
    cartitem.quantity = quantity
    print('cart_item', cartitem.cart)
    cartitem.save()

    serializer = CartSerializer(cartitem.cart)
    return Response({"data": serializer.data, "message": "Cart updated successfully"})

@api_view(['DELETE'])
def remove_cart_item(request):
    cart_code = request.data.get('cart_code')
    cart_item_id = request.data.get('record_id')
    quantity = int(request.data.get('quantity'))
    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart_item = CartItem.objects.get(id=cart_item_id, cart=cart)
        cart_item.quantity -= quantity
        if cart_item.quantity > 0:
            cart_item.save()
        else:
            cart_item.delete()
        return Response({"message": "Cart item removed successfully"}, status=200)
    except Cart.DoesNotExist:
        return Response({"error": "Cart not found"}, status=404)
    except CartItem.DoesNotExist:
        return Response({"error": "Cart item not found"}, status=404)
    
@api_view(['DELETE'])
def remove_all_cart_items(request):
    cart_code = request.data.get('cart_code')
    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart.cart_items.all().delete()
        return Response({"message": "All cart items removed successfully"}, status=200)
    except Cart.DoesNotExist:
        return Response({"error": "Cart not found"}, status=404)

@api_view(['DELETE'])
def delete_cart(request):
    cart_code = request.data.get('cart_code')
    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart.delete()
        return Response({"message": "Cart deleted successfully"}, status=200)
    except Cart.DoesNotExist:
        return Response({"error": "Cart not found"}, status=404)

@api_view(['POST'])
def add_to_wishlist(request):
    email = request.data.get('email')
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')
    user = User.objects.get(email=email) if email else None
    
    if not record_id:
        return Response({"error": "record_id is required"}, status=400)

    if wishlist_code:
        wishlist, _ = Wishlist.objects.get_or_create(wishlist_code=wishlist_code, defaults={'user': user})
    else:
        wishlist = Wishlist.objects.create(user=user)
    record = Record.objects.get(id=str(record_id))
    _, created = WishlistItem.objects.get_or_create(wishlist=wishlist, record=record)
    if not created:
        return Response({"message": "Record already in wishlist"}, status=200)
    serializer = WishlistSerializer(wishlist)
    return Response({"message": "Record added to wishlist", "wishlist": serializer.data}, status=201)

@api_view(['GET'])
def get_all_wishlists(_):
    wishlists = Wishlist.objects.all()
    serializer = WishlistSerializer(wishlists, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_wishlist(request, wishlist_code):
    email = request.query_params.get('email')
    user = User.objects.get(email=email) if email else None
    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    serializer = WishlistSerializer(wishlist)
    return Response(serializer.data)

@api_view(['DELETE'])
def remove_from_wishlist(request):
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')

    if not wishlist_code or not record_id:
        return Response({"error": "wishlist_code and record_id are required"}, status=400)

    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    try:
        wishlist_item = WishlistItem.objects.get(wishlist=wishlist, record_id=record_id)
    except WishlistItem.DoesNotExist:
        return Response({"error": "Wishlist item not found"}, status=404)

    wishlist_item.delete()
    wishlist.refresh_from_db()
    serializer = WishlistSerializer(wishlist)
    return Response({"message": "Record removed from wishlist", "wishlist": serializer.data}, status=200)

@api_view(['GET'])
def get_wishlist_count(request):
    wishlist_code = request.query_params.get('wishlist_code') or request.data.get('wishlist_code')
    if not wishlist_code:
        return Response({"error": "wishlist_code is required"}, status=400)

    wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
    wishlist_count = wishlist.wishlist_items.count()
    return Response({"wishlist_count": wishlist_count}, status=200)

@api_view(['POST'])
def add_review(request):
    record_id = request.data.get('record_id')
    email = request.data.get('email')
    rating = request.data.get('rating')
    review = request.data.get('review')

    if(not record_id or not email or not rating or not review):
        return Response({"error": "record_id, email, rating, and review are required"}, status=400)
    
    if(int(rating) < 1 or int(rating) > 5):
        return Response({"error": "rating must be between 1 and 5"}, status=400)

    if Review.objects.filter(record_id=record_id, email=email).exists():
        return Response({"error": "User has already reviewed this record"}, status=400)

    record = Record.objects.get(id=str(record_id))
    user = User.objects.get(email=email)

    new_review = Review.objects.create(
        record=record,
        user=user,
        rating=rating,
        review=review
    )
    serialized_review = ReviewSerializer(new_review)
    return Response({"message": "Review added successfully", "review": serialized_review.data}, status=201)

@api_view(['PUT'])
def update_review(request):
    review_id = request.data.get('review_id')
    rating = request.data.get('rating')
    review_text = request.data.get('review')

    if not review_id:
        return Response({"error": "review_id is required"}, status=400)

    try:
        review = Review.objects.get(id=review_id)
    except Review.DoesNotExist:
        return Response({"error": "Review not found"}, status=404)

    if rating:
        if int(rating) < 1 or int(rating) > 5:
            return Response({"error": "rating must be between 1 and 5"}, status=400)
        review.rating = rating

    if review_text:
        review.review = review_text

    review.save()
    serialized_review = ReviewSerializer(review)
    return Response({"message": "Review updated successfully", "review": serialized_review.data}, status=200)

@api_view(['DELETE'])
def delete_review(request):
    review_id = request.data.get('review_id')

    if not review_id:
        return Response({"error": "review_id is required"}, status=400)

    try:
        review = Review.objects.get(id=review_id)
    except Review.DoesNotExist:
        return Response({"error": "Review not found"}, status=404)

    review.delete()
    return Response({"message": "Review deleted successfully"}, status=200)

@api_view(['GET'])
def get_record_reviews(request, record_id):
    try:
        record = Record.objects.get(id=record_id)
    except Record.DoesNotExist:
        return Response({"error": "Record not found"}, status=404)

    reviews = Review.objects.filter(record=record)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(reviews, request)
    serializer = ReviewSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

@api_view(['GET'])
def get_all_reviews(request):
    reviews = Review.objects.all()
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(reviews, request)
    serializer = ReviewSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)

@api_view(['GET'])
def record_search(request):
    query = request.query_params.get('query')
    if not query:
        return Response({"error": "query parameter is required"}, status=400)
    
    records = Record.objects.filter(Q(title__icontains=query) |
                                    Q(artist__name__icontains=query) |
                                    Q(genere__name__icontains=query) | 
                                    Q(category__name__icontains=query))
    
    
    if not records.exists():
        return Response({"message": "No records found matching the query"}, status=404)
    serializer = RecordListSerializer(records, many=True)
    return Response(serializer.data)


@api_view(['POST'])
def create_stripe_checkout_session(request):
    cart_code = request.data.get('cart_code')
    email = request.data.get('email')
    cart = Cart.objects.get(cart_code=cart_code)

    if not cart or cart.cart_items.count() == 0:
        return Response({"error": "Cart is empty or not found"}, status=400)
    
    try:
        line_items = []
        for item in cart.cart_items.all():
            line_items.append({
                'price_data': {
                    'currency': 'mxn',
                    'product_data': {
                        'name': item.record.title,
                    },
                    'unit_amount': int(item.record.price * 100),
                },
                'quantity': item.quantity,
            })

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            success_url='https://yourdomain.com/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://yourdomain.com/cancel',
            customer_email=email,
            metadata={'cart_code': cart_code}
        )
        return Response({"checkout_url": checkout_session}, status=200)
    except Exception as e:
        return Response({"error": str(e)}, status=500)
    

def fulfill_checkout(session, cart_code):
    order = Order.objects.create(
                                stripe_checkout_session_id=session['id'],
                                amount=session['amount_total'],
                                currency=session['currency'],
                                user_email=session['customer_email'],
                                status='paid')
    cart = Cart.objects.get(cart_code=cart_code)
    cart_items = cart.cart_items.all()

    for item in cart_items:
        OrderItem.objects.create(
            order=order,
            record=item.record,
            quantity=item.quantity,
            price=item.record.price
        )

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META['HTTP_STRIPE_SIGNATURE']
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        return HttpResponse(status=400)

    if event['type'] == 'checkout.session.completed' or event['type'] == 'payment_intent.succeeded':
        session = event['data']['object']
        cart_code = session['metadata']['cart_code']
        fulfill_checkout(session, cart_code)
        

    return HttpResponse(status=200)
