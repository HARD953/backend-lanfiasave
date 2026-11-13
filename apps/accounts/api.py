import secrets

from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.audit import audit

from .models import Membership, User
from .roles import ROLE_PERMISSIONS


class MembershipSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    program_name = serializers.CharField(source="program.name", read_only=True, default=None)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "organization", "organization_name", "role", "role_label", "program", "program_name",
                  "permissions"]

    def get_permissions(self, obj):
        return sorted(ROLE_PERMISSIONS.get(obj.role, set()))


class MeSerializer(serializers.ModelSerializer):
    memberships = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    kind = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "email", "phone", "kind", "must_change_password", "memberships"]

    def get_memberships(self, obj):
        return MembershipSerializer(obj.active_memberships(), many=True).data

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_kind(self, obj):
        return "beneficiary" if obj.is_beneficiary else "staff"


def _tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class LoginView(APIView):
    """Connexion du personnel (identifiant + mot de passe). Les échecs sont journalisés (GOV-03)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is None or user.is_beneficiary:
            audit("auth.login_failed", outcome="denied", data={"username": username[:80]})
            return Response({"error": {"code": "invalid_credentials", "message": "Identifiants incorrects."}},
                            status=status.HTTP_401_UNAUTHORIZED)
        audit("auth.login", actor=user)
        return Response({**_tokens_for(user), "user": MeSerializer(user).data})


class BeneficiaryLoginView(APIView):
    """Connexion bénéficiaire : code dossier (LFS-XXXX-XXXX) ou téléphone + code PIN."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        identifier = (request.data.get("identifier") or "").strip().upper().replace(" ", "")
        pin = (request.data.get("pin") or "").strip()
        user = (User.objects.filter(beneficiary__isnull=False, is_active=True)
                .filter(Q(username=identifier) | Q(phone=identifier)).first())
        if user is None or not user.check_password(pin):
            audit("auth.beneficiary_login_failed", outcome="denied")
            return Response({"error": {"code": "invalid_credentials",
                                       "message": "Code dossier ou code PIN incorrect."}},
                            status=status.HTTP_401_UNAUTHORIZED)
        audit("auth.beneficiary_login", actor=user)
        return Response({**_tokens_for(user), "user": MeSerializer(user).data})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


@transaction.atomic
def grant_beneficiary_access(person, agent) -> str:
    """Crée ou réinitialise l'accès bénéficiaire. Le PIN est renvoyé une seule fois à l'agent."""
    pin = f"{secrets.randbelow(10**6):06d}"
    user = getattr(person, "account", None)
    if user is None:
        user = User(username=person.public_code, beneficiary=person, first_name=person.first_name,
                    last_name=person.last_name, phone=person.phone or "")
    user.set_password(pin)
    user.must_change_password = False
    user.save()
    audit("beneficiary.access_granted", obj=person, actor=agent)
    return pin
