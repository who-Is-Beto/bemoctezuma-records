from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from .models import Record, Category, Cart, CartItem, Wishlist, WishlistItem, Review
from .serilizers import RecordDetailSerializer, RecordListSerializer, CategorySerializer, CategoryListSerializer, CartSerializer, CartItemSerializer, WishlistSerializer, ReviewSerializer
from rest_framework.response import Response
from django.contrib.auth import get_user_model

User = get_user_model()

@api_view(['GET'])
def record_list(_):
    records = Record.objects.filter(featured=True)
    serializer = RecordListSerializer(records, many=True)
    return Response(serializer.data)

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
    item_id = request.data.get('item_id')

    cart, _ = Cart.objects.get_or_create(cart_code=cart_code)
    record = Record.objects.get(id=str(item_id))

    cart_item, _ = CartItem.objects.get_or_create(record=record, cart=cart)
    cart_item.quantity = 1
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
    cart_item_id = request.data.get('item_id')
    quantity = int(request.data.get('quantity'))
    try:
        cartitem = CartItem.objects.get(id=cart_item_id)
    except CartItem.DoesNotExist:
        return Response({"error": "Cart item not found"}, status=404)
    if quantity >= 1:
        cartitem.quantity = cartitem.quantity - quantity
        cartitem.save()
        message = "Cart item quantity decreased"
    else: 
        cartitem.delete()
        message = "Cart item removed"
    serializer = CartItemSerializer(cartitem)
    return Response({"data": serializer.data, "message": message}, status=200)

@api_view(['DELETE'])
def remove_all_cart_items(request):
    cart_code = request.data.get('cart_code')
    try:
        cart = Cart.objects.get(cart_code=cart_code)
        cart_items = CartItem.objects.filter(cart=cart)
        cart_items.delete()
        return Response({"message": "All cart items removed successfully"}, status=200)
    except Cart.DoesNotExist:
        return Response({"error": "Cart not found"}, status=404)
    


@api_view(['POST'])
def add_to_wishlist(request):
    wishlist_code = request.data.get('wishlist_code')
    record_id = request.data.get('record_id')

    if not record_id:
        return Response({"error": "record_id is required"}, status=400)

    record = get_object_or_404(Record, id=str(record_id))

    if wishlist_code:
        wishlist = get_object_or_404(Wishlist, wishlist_code=wishlist_code)
        created = False
    else:
        wishlist = Wishlist.objects.create()
        created = True

    wish_item, _ = WishlistItem.objects.get_or_create(record=record, wishlist=wishlist)

    wishlist.refresh_from_db()

    serializer = WishlistSerializer(wishlist)
    status_code = 201 if created else 200
    return Response(serializer.data, status=status_code)

@api_view(['GET'])
def get_all_wishlists(_):
    wishlists = Wishlist.objects.all()
    serializer = WishlistSerializer(wishlists, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_wishlist(_, wishlist_code):
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
def get_record_reviews(_, record_id):
    try:
        record = Record.objects.get(id=record_id)
    except Record.DoesNotExist:
        return Response({"error": "Record not found"}, status=404)

    reviews = Review.objects.filter(record=record)
    serializer = ReviewSerializer(reviews, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def get_all_reviews(_):
    reviews = Review.objects.all()
    serializer = ReviewSerializer(reviews, many=True)
    return Response(serializer.data)
