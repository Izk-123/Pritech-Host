from django import forms
from users.models import User, CustomerProfile

class CheckoutForm(forms.ModelForm):
    """
    Form for capturing customer details during checkout.
    Maps to SRS FR-82 (Full name, phone, DOB, gender required) and FR-15 (Payment method).
    Handles both guest checkouts and logged-in users upgrading/buying a new plan.
    """
    payment_method = forms.ChoiceField(
        choices=[('airtel', 'Airtel Money'), ('tnm', 'TNM Mpamba'), ('card', 'Card')],
        widget=forms.RadioSelect(),
        initial='airtel',
        help_text="Select how you want to pay the 25% deposit."
    )
    requested_domain = forms.CharField(
        max_length=255, required=False, label="Domain you want",
        widget=forms.TextInput(attrs={'placeholder': 'yourbusiness.com'})
    )
    company_name = forms.CharField(
        max_length=255, required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Company (optional)'})
    )
    accept_terms = forms.BooleanField(
        required=True, label="I accept the terms of service and privacy policy."
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone_number', 'date_of_birth', 'gender']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'gender': forms.RadioSelect(),
            'first_name': forms.TextInput(attrs={'placeholder': 'John Doe'}),
            'phone_number': forms.TextInput(attrs={'placeholder': '+265 999 000 000'}),
            'email': forms.EmailInput(attrs={'placeholder': 'john@example.com'}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('current_user', None)
        super().__init__(*args, **kwargs)
        
        if self.current_user and self.current_user.is_authenticated:
            self.fields['email'].initial = self.current_user.email
            self.fields['email'].widget.attrs['readonly'] = True
            self.fields['first_name'].initial = self.current_user.first_name
            self.fields['last_name'].initial = self.current_user.last_name
            self.fields['phone_number'].initial = self.current_user.phone_number
            self.fields['date_of_birth'].initial = self.current_user.date_of_birth
            self.fields['gender'].initial = self.current_user.gender

        self.fields['email'].required = True
        self.fields['first_name'].required = True
        self.fields['phone_number'].required = True
        self.fields['date_of_birth'].required = True
        self.fields['gender'].required = True

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not self.current_user or not self.current_user.is_authenticated:
            if User.objects.filter(email=email).exists():
                raise forms.ValidationError("An account with this email already exists. Please sign in.")
        return email


class UserProfileForm(forms.ModelForm):
    """
    Form to update core user details.
    SRS FR-81: Full name, DOB, gender, phone number.
    """
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'gender': forms.RadioSelect(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != 'gender':
                field.widget.attrs.update({
                    'class': 'w-full border border-border rounded-lg p-3 focus:outline-none focus:border-brand'
                })

    def clean_first_name(self):
        first_name = self.cleaned_data.get('first_name')
        if not first_name:
            raise forms.ValidationError("First name is required.")
        return first_name


class CustomerProfileForm(forms.ModelForm):
    """
    Form to update company and billing details.
    SRS FR-4: Customer type, contact details, billing details.
    """
    class Meta:
        model = CustomerProfile
        fields = ['company_name', 'tin_vat', 'address']
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({
                'class': 'w-full border border-border rounded-lg p-3 focus:outline-none focus:border-brand'
            })