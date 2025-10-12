from rest_framework.decorators import api_view
from .models import Record, Category, Cart, CartItem
from .serilizers import RecordDetailSerializer, RecordListSerializer, CategorySerializer, CategoryListSerializer, CartSerializer, CartItemSerializer
from rest_framework.response import Response

# Create your views here.

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
    
