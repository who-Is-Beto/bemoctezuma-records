"""Review views: add, update, delete and list record reviews."""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .common import error_response
from ..models import Record, Review, User
from ..pagination import StandardResultsSetPagination
from ..serilizers import ReviewSerializer


@api_view(['POST'])
def add_review(request):
    record_id = request.data.get('record_id')
    email = request.data.get('email')
    rating = request.data.get('rating')
    review = request.data.get('review')

    if(not record_id or not email or not rating or not review):
        return error_response(
            "record_id, email, rating, and review are required",
            status_code=400,
            code="review_params_required",
        )
    
    if(int(rating) < 1 or int(rating) > 5):
        return error_response("rating must be between 1 and 5", status_code=400, code="rating_invalid")

    if Review.objects.filter(record_id=record_id, email=email).exists():
        return error_response("User has already reviewed this record", status_code=400, code="review_duplicate")

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
        return error_response("review_id is required", status_code=400, code="review_id_required")

    try:
        review = Review.objects.get(id=review_id)
    except Review.DoesNotExist:
        return error_response("Review not found", status_code=404, code="review_not_found")

    if rating:
        if int(rating) < 1 or int(rating) > 5:
            return error_response("rating must be between 1 and 5", status_code=400, code="rating_invalid")
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
        return error_response("review_id is required", status_code=400, code="review_id_required")

    try:
        review = Review.objects.get(id=review_id)
    except Review.DoesNotExist:
        return error_response("Review not found", status_code=404, code="review_not_found")

    review.delete()
    return Response({"message": "Review deleted successfully"}, status=200)


@api_view(['GET'])
def get_record_reviews(request, record_id):
    try:
        record = Record.objects.get(id=record_id)
    except Record.DoesNotExist:
        return error_response("Record not found", status_code=404, code="product_not_found")

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