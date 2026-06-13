from django.db import models
from django_tenants.models import TenantMixin, DomainMixin

from apps.core.utils import comprime_imagem


class TenantCompany(TenantMixin):
    PLAN_CHOICES = [
        ('basic', 'Básico — até 10 veículos'),
        ('professional', 'Profissional — até 30 veículos'),
        ('premium', 'Premium — ilimitado'),
    ]

    name = models.CharField('Nome da locadora', max_length=200)
    slug = models.SlugField(unique=True)
    cnpj = models.CharField('CNPJ', max_length=18, blank=True)
    phone = models.CharField('Telefone', max_length=20, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to='tenants/logos/', blank=True, null=True)
    plan = models.CharField('Plano', max_length=20, choices=PLAN_CHOICES, default='basic')
    is_active = models.BooleanField(default=True)
    trial_ends_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    auto_create_schema = True

    class Meta:
        verbose_name = 'Locadora'
        verbose_name_plural = 'Locadoras'

    def save(self, *args, **kwargs):
        if self.logo and not getattr(self.logo, '_committed', True):
            comprimida = comprime_imagem(self.logo, max_dim=800, quality=85)
            if comprimida:
                self.logo = comprimida
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass
