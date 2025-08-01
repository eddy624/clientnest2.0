from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from .models import SocialAccount, PostAnalytics, Comment
from .serializers import SocialAccountSerializer, PostAnalyticsSerializer, CommentSerializer
from .instagram_service import InstagramService
from rest_framework.views import APIView
from .x_service import XService
from rest_framework_simplejwt.authentication import JWTAuthentication
from .facebook_service import FacebookService
from .linkedin_service import LinkedInService
import requests
import logging
from requests.exceptions import RequestException
from rest_framework.exceptions import NotFound
from functools import wraps
from django.shortcuts import get_object_or_404
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from user_service.mixins import SwaggerFakeViewMixin
from .youtube_service import YouTubeService
import os
from .bluesky_service import BlueskyService
from .threads_service import ThreadsService
from .pinterest_service import PinterestService
import json
import tempfile

# Create your views here.

logger = logging.getLogger(__name__)

def handle_api_errors(view_func):
    @wraps(view_func)
    def _wrapped_view(self, request, *args, **kwargs):
        try:
            return view_func(self, request, *args, **kwargs)
        except RequestException as e:
            logger.error("API error: %s", e)
            return Response({
                'status': 'error',
                'message': 'Failed to connect to external API',
                'details': str(e)
            }, status=status.HTTP_502_BAD_GATEWAY)
        except NotFound as e:
            logger.warning("Known HTTP exception: %s", e)
            raise e
        except Exception as e:
            logger.exception("Unexpected error in %s", self.__class__.__name__)
            return Response({
                'status': 'error',
                'message': 'An unexpected error occurred'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return _wrapped_view

class SocialAccountViewSet(SwaggerFakeViewMixin, viewsets.ModelViewSet):
    serializer_class = SocialAccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def _get_actual_queryset(self):
        return SocialAccount.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'])
    def refresh_token(self, request, pk=None):
        account = self.get_object()
        if account.platform == 'instagram':
            try:
                instagram_service = InstagramService(account)
                instagram_service.refresh_token()
                return Response({'status': 'Token refreshed successfully'})
            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(
            {'error': 'Token refresh not supported for this platform'},
            status=status.HTTP_400_BAD_REQUEST
        )

    @action(detail=True, methods=['post'])
    def disconnect(self, request, pk=None):
        account = self.get_object()
        account.is_active = False
        account.save()
        return Response({'status': 'Account disconnected'})

    @action(detail=True, methods=['get'])
    def account_info(self, request, pk=None):
        account = self.get_object()
        if account.platform == 'instagram':
            try:
                instagram_service = InstagramService(account)
                info = instagram_service.get_account_info()
                return Response(info)
            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(
            {'error': 'Account info not supported for this platform'},
            status=status.HTTP_400_BAD_REQUEST
        )

class PostAnalyticsViewSet(SwaggerFakeViewMixin, viewsets.ModelViewSet):
    serializer_class = PostAnalyticsSerializer
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def _get_actual_queryset(self):
        return PostAnalytics.objects.filter(
            post__user=self.request.user
        ).select_related('social_account')

    @action(detail=True, methods=['post'])
    def refresh_analytics(self, request, pk=None):
        analytics = self.get_object()
        if analytics.social_account.platform == 'instagram':
            try:
                instagram_service = InstagramService(analytics.social_account)
                instagram_service.update_post_analytics(analytics.post.id)
                return Response({'status': 'Analytics refreshed successfully'})
            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(
            {'error': 'Analytics refresh not supported for this platform'},
            status=status.HTTP_400_BAD_REQUEST
        )

class XConnectionTestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]
    
    @handle_api_errors
    def get(self, request):
        """Test X connection and return account info"""
        # Get the user's X account
        x_account = SocialAccount.objects.filter(
            user=request.user,
            platform='x',
            is_active=True
        ).first()
        
        if not x_account:
            return Response({
                'status': 'error',
                'message': 'No active X account found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Initialize X service with both tokens
        x_service = XService(
            access_token=x_account.access_token,
            access_token_secret=x_account.access_token_secret
        )
        
        # Get account info
        account_info = x_service.get_account_info()
        
        return Response({
            'status': 'success',
            'message': 'X account is connected',
            'account_info': account_info
        })

class XPostTestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]
    
    @handle_api_errors
    def post(self, request):
        """Test posting to X"""
        # Get the user's X account
        x_account = SocialAccount.objects.filter(
            user=request.user,
            platform='x',
            is_active=True
        ).first()
        
        if not x_account:
            return Response({
                'status': 'error',
                'message': 'No active X account found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get the content from the request
        content = request.data.get('content')
        if not content:
            return Response({
                'status': 'error',
                'message': 'No content provided'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Initialize X service with both tokens
        x_service = XService(
            access_token=x_account.access_token,
            access_token_secret=x_account.access_token_secret
        )
        
        # Post the content
        result = x_service.post_content(content)
        
        return Response({
            'status': 'success',
            'message': 'Tweet posted successfully',
            'tweet_id': result.get('id'),
            'text': result.get('text'),
            'created_at': result.get('created_at')
        })

def get_linkedin_account(request):
    linkedin_account = SocialAccount.objects.filter(
        user=request.user,
        platform='linkedin',
        is_active=True
    ).first()
    if not linkedin_account:
        raise NotFound({'status': 'error', 'message': 'No active LinkedIn account found'})
    return linkedin_account

class LinkedInConnectionTestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def get(self, request):
        try:
            linkedin_account = get_linkedin_account(request)
            linkedin_service = LinkedInService(linkedin_account)
            account_info = linkedin_service.get_account_info()
            return Response({
                'status': 'success',
                'message': 'LinkedIn account is connected',
                'account_info': account_info
            })
        except RequestException as e:
            logger.error("LinkedIn API error: %s", e)
            return Response({
                'status': 'error',
                'message': 'Failed to connect to LinkedIn API',
                'details': str(e)
            }, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.exception("Unexpected error in LinkedInConnectionTestView")
            return Response({
                'status': 'error',
                'message': 'An unexpected error occurred'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class LinkedInPostView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def post(self, request):
        try:
            linkedin_account = get_linkedin_account(request)
            content = request.data.get('content')
            if not content:
                return Response({
                    'status': 'error',
                    'message': 'No content provided'
                }, status=status.HTTP_400_BAD_REQUEST)
            linkedin_service = LinkedInService(linkedin_account)
            result = linkedin_service.post_content(content)
            return Response({
                'status': 'success',
                'message': 'Post published successfully',
                'post_id': result.get('id')
            }, status=status.HTTP_201_CREATED)
        except RequestException as e:
            logger.error("LinkedIn API error: %s", e)
            return Response({
                'status': 'error',
                'message': 'Failed to connect to LinkedIn API',
                'details': str(e)
            }, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.exception("Unexpected error in LinkedInPostView")
            return Response({
                'status': 'error',
                'message': 'An unexpected error occurred'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class LinkedInUserInfoView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def get(self, request):
        try:
            linkedin_account = get_linkedin_account(request)
            linkedin_service = LinkedInService(linkedin_account)
            userinfo = linkedin_service.get_userinfo()
            return Response({
                'status': 'success',
                'userinfo': userinfo
            })
        except RequestException as e:
            logger.error("LinkedIn API error: %s", e)
            return Response({
                'status': 'error',
                'message': 'Failed to connect to LinkedIn API',
                'details': str(e)
            }, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.exception("Unexpected error in LinkedInUserInfoView")
            return Response({
                'status': 'error',
                'message': 'An unexpected error occurred'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class LinkedInImagePostView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def post(self, request):
        linkedin_account = get_linkedin_account(request)
        content = request.data.get('content')
        image = request.FILES.get('image')
        if not content or not image:
            return Response({
                'status': 'error',
                'message': 'Content and image are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        linkedin_service = LinkedInService(linkedin_account)
        # Step 1: Register image upload
        upload_resp = linkedin_service.register_image_upload()
        upload_url = upload_resp['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
        asset_urn = upload_resp['value']['asset']
        # Step 2: Upload the image using a temporary file path
        temp_file_path = None
        try:
            with image.file as opened_file, tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(opened_file.read())
                temp_file_path = temp_file.name
            linkedin_service.upload_image(upload_url, temp_file_path)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        # Step 3: Post content with image
        result = linkedin_service.post_content_with_image(content, asset_urn)
        return Response({
            'status': 'success',
            'message': 'Post with image published successfully',
            'post_id': result.get('id'),
            'asset_urn': asset_urn
        }, status=status.HTTP_201_CREATED)

class FacebookConnectionTestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def get(self, request):
        """Test Facebook connection and return account info"""
        try:
            fb_account = SocialAccount.objects.filter(
                user=request.user,
                platform='facebook',
                is_active=True
            ).first()

            if not fb_account:
                return Response({
                    'status': 'error',
                    'message': 'No active Facebook account found'
                }, status=status.HTTP_404_NOT_FOUND)

            fb_service = FacebookService(fb_account.access_token)
            account_info = fb_service.get_account_info()

            return Response({
                'status': 'success',
                'message': 'Facebook account is connected',
                'account_info': account_info
            })

        except RequestException as e:
            logger.error("Facebook API error: %s", e)
            return Response({
                'status': 'error',
                'message': 'Failed to connect to Facebook API',
                'details': str(e)
            }, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.exception("Unexpected error in FacebookConnectionTestView")
            return Response({
                'status': 'error',
                'message': 'An unexpected error occurred'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SocialAccountsStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def get(self, request):
        result = {}
        # X account
        x_account = SocialAccount.objects.filter(
            user=request.user,
            platform='x',
            is_active=True
        ).first()
        if x_account:
            x_service = XService(
                access_token=x_account.access_token,
                access_token_secret=x_account.access_token_secret
            )
            result['x_account'] = {
                'status': 'connected',
                'account_info': x_service.get_account_info()
            }
        else:
            result['x_account'] = {'status': 'not connected'}

        # LinkedIn account
        try:
            linkedin_account = get_linkedin_account(request)
            linkedin_service = LinkedInService(linkedin_account)
            result['linkedin_account'] = {
                'status': 'connected',
                'account_info': linkedin_service.get_account_info()
            }
        except NotFound:
            result['linkedin_account'] = {'status': 'not connected'}

        return Response(result)

class XAndLinkedInConnectionTestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @handle_api_errors
    def get(self, request):
        response_data = {
            'status': 'success',
            'x_account_info': None,
            'linkedin_account_info': None,
            'message': ''
        }
        messages = []
        # X account
        x_account = SocialAccount.objects.filter(
            user=request.user,
            platform='x',
            is_active=True
        ).first()
        if x_account:
            x_service = XService(
                access_token=x_account.access_token,
                access_token_secret=x_account.access_token_secret
            )
            try:
                response_data['x_account_info'] = x_service.get_account_info()
                messages.append('X account is connected')
            except Exception as e:
                messages.append(f'Failed to fetch X account info: {str(e)}')
        else:
            messages.append('No active X account found')

        # LinkedIn account
        try:
            linkedin_account = get_linkedin_account(request)
            linkedin_service = LinkedInService(linkedin_account)
            response_data['linkedin_account_info'] = linkedin_service.get_account_info()
            messages.append('LinkedIn account is connected')
        except NotFound:
            messages.append('No active LinkedIn account found')
        except Exception as e:
            messages.append(f'Failed to fetch LinkedIn account info: {str(e)}')

        response_data['message'] = '; '.join(messages)
        return Response(response_data)

class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.all().order_by('-created_at')
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class YouTubeVideoUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def post(self, request):
        # Expecting: file, title, description, tags (optional)
        file = request.FILES.get('file')
        title = request.data.get('title')
        description = request.data.get('description')
        tags = request.data.get('tags', '').split(',') if request.data.get('tags') else []
        if not file or not title or not description:
            return Response({'status': 'error', 'message': 'file, title, and description are required'}, status=400)
        # Save file temporarily
        # Extract file extension from uploaded file name, default to .mp4 if not available
        original_name = getattr(file, 'name', None)
        ext = os.path.splitext(original_name)[1].lower() if original_name else '.mp4'
        # Validate file extension against an allowlist of safe video formats
        # Get allowed extensions from environment variable or use default
        allowed_extensions_env = os.environ.get('YOUTUBE_ALLOWED_EXTENSIONS', '.mp4,.avi,.mov,.mkv')
        allowed_extensions = set(ext.strip().lower() for ext in allowed_extensions_env.split(','))
        if ext not in allowed_extensions:
            return Response({'status': 'error', 'message': f'Invalid file extension: {ext}. Allowed extensions are {", ".join(allowed_extensions)}'}, status=400)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                for chunk in file.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name
            # You should set these paths in your settings or env
            credentials_path = os.environ.get('YOUTUBE_CREDENTIALS_PATH', 'youtube_client_secret.json')
            token_path = os.environ.get('YOUTUBE_TOKEN_PATH', 'youtube_token.json')
            yt_service = YouTubeService(credentials_path, token_path)
            response = yt_service.upload_video(tmp_path, title, description, tags=tags)
            return Response({'status': 'success', 'video_id': response.get('id')})
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

class BlueskyPostView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def post(self, request):
        identifier = request.data.get('identifier')
        password = request.data.get('password')
        text = request.data.get('text')
        if not identifier or not password or not text:
            return Response({'status': 'error', 'message': 'identifier, password, and text are required'}, status=400)
        try:
            service = BlueskyService(identifier, password)
            result = service.post(text)
            return Response({'status': 'success', 'uri': result['uri']})
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)

class ThreadsPostView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        text = request.data.get('text')
        if not username or not password or not text:
            return Response({'status': 'error', 'message': 'username, password, and text are required'}, status=400)
        try:
            service = ThreadsService(username, password)
            result = service.post(text)
            return Response({'status': 'success', 'thread_id': result.get('id')})
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)

class PinterestPinCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    def post(self, request):
        client_id = request.data.get('client_id')
        client_secret = request.data.get('client_secret')
        redirect_uri = request.data.get('redirect_uri')
        token = request.data.get('token')
        board_id = request.data.get('board_id')
        image_url = request.data.get('image_url')
        title = request.data.get('title')
        description = request.data.get('description')
        link = request.data.get('link')
        if not all([client_id, client_secret, redirect_uri, token, board_id, image_url, title, description]):
            return Response({'status': 'error', 'message': 'Missing required fields'}, status=400)
        try:
            parsed_token = None
            try:
                parsed_token = json.loads(token)
            except json.JSONDecodeError:
                return Response({'status': 'error', 'message': 'Invalid token format'}, status=400)
            service = PinterestService(client_id, client_secret, redirect_uri, token=parsed_token)
            result = service.create_pin(board_id, image_url, title, description, link)
            return Response({'status': 'success', 'pin_id': result.get('id')})
        except Exception as e:
            return Response({'status': 'error', 'message': str(e)}, status=500)
