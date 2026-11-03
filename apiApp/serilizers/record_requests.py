from urllib.parse import urlparse

from rest_framework import serializers


def _https_on(host):
    def check(value):
        if value and (urlparse(value).scheme != 'https' or urlparse(value).hostname != host):
            raise serializers.ValidationError(f"Solo enlaces de {host}.")
    return check


class RecordRequestSerializer(serializers.Serializer):
    """"Encargar un disco": a visitor asks the store to find a record."""
    name = serializers.CharField(max_length=80, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    artist = serializers.CharField(max_length=120, trim_whitespace=True)
    title = serializers.CharField(max_length=160, trim_whitespace=True)
    # Free text: the form offers the store's formats plus "Cualquiera".
    format = serializers.CharField(max_length=60, required=False, allow_blank=True, default='')
    details = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    message = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')
    # Filled when the visitor picked the record from the Discogs search.
    # Only Discogs links: they end up as a link and an image in our inbox.
    year = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=1900, max_value=2100)
    discogs_url = serializers.URLField(required=False, allow_blank=True, default='', max_length=300,
                                       validators=[_https_on('www.discogs.com')])
    cover_url = serializers.URLField(required=False, allow_blank=True, default='', max_length=500,
                                     validators=[_https_on('i.discogs.com')])
    # Honeypot: hidden in the form; bots fill it, people don't.
    website = serializers.CharField(required=False, allow_blank=True, default='')
