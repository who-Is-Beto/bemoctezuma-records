from rest_framework.decorators import api_view
from .models import Record, Category
from .serilizers import RecordDetailSerializer, RecordListSerializer, CategorySerializer, CategoryListSerializer
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
def get_category_list ():
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