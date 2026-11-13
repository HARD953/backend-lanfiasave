from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel

from .definitions import SPECIAL_VALUES


class QuestionnaireVersion(BaseModel):
    """Questionnaire versionné (REC-02, REF-01). Une version publiée n'est jamais modifiée."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        PUBLISHED = "published", "Publié"
        RETIRED = "retired", "Retiré"

    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    definition = models.JSONField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["code", "-version"]
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="uniq_questionnaire_version")]

    def __str__(self):
        return f"{self.code} v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = QuestionnaireVersion.objects.get(pk=self.pk)
            if previous.status != self.Status.DRAFT and previous.definition != self.definition:
                raise ValidationError("Une version publiée est figée : créez une nouvelle version.")
        super().save(*args, **kwargs)

    def questions(self) -> dict:
        return {qq["code"]: qq for s in self.definition["sections"] for qq in s["questions"]}

    def validate_answers(self, answers: dict) -> None:
        """Contrôle de type. Les réponses absentes restent absentes : rien n'est inventé."""
        questions = self.questions()
        errors = {}
        for code, value in answers.items():
            question = questions.get(code)
            if question is None:
                errors[code] = "Question inconnue pour cette version."
                continue
            if value in SPECIAL_VALUES:
                continue
            t = question["type"]
            if t == "single" and value not in {o["value"] for o in question.get("options", [])}:
                errors[code] = "Choix invalide."
            elif t == "number" and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                errors[code] = "Nombre positif attendu."
            elif t == "text" and not isinstance(value, str):
                errors[code] = "Texte attendu."
        if errors:
            raise ValidationError({"answers": [f"{k} : {v}" for k, v in errors.items()]})


class SurveyResponse(BaseModel):
    """Réponses d'une enquête, rattachées à la version exacte du questionnaire."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="survey_responses")
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="survey_responses")
    household = models.ForeignKey("beneficiaries.Household", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="survey_responses")
    questionnaire = models.ForeignKey(QuestionnaireVersion, on_delete=models.PROTECT, related_name="responses")
    answers = models.JSONField(default=dict)
    collected_at = models.DateTimeField()
    collected_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")
    device_id = models.CharField(max_length=80, blank=True)
    client_uuid = models.UUIDField(null=True, blank=True, unique=True,
                                   help_text="Identifiant généré par le terminal : empêche les doublons de synchro")

    class Meta:
        ordering = ["-collected_at"]

    def clean(self):
        self.questionnaire.validate_answers(self.answers or {})
