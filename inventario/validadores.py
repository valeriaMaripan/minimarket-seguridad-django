"""Fase 5: reglas adicionales para las contraseñas."""
import re

from django.core.exceptions import ValidationError


class LetrasYNumerosValidator:
    """La contraseña debe combinar al menos una letra y un número."""

    def validate(self, password, user=None):
        if not re.search(r'[A-Za-zÁÉÍÓÚáéíóúÑñ]', password) or not re.search(r'\d', password):
            raise ValidationError('La contraseña debe tener al menos una letra y un número.',
                                  code='sin_letras_y_numeros')

    def get_help_text(self):
        return 'Tu contraseña debe combinar letras y números.'
