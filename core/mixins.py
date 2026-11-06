from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """SRS FR-42: Staff permissions"""
    def test_func(self):
        return self.request.user.is_staff

class OwnerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Ensures users only access their own data (SRS NFR-15)"""
    def test_func(self):
        obj = self.get_object()
        return obj.customer == self.request.user