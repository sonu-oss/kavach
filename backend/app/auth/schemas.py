from pydantic import BaseModel, Field


class SignupIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=256)
    full_name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=30)


class LoginIn(BaseModel):
    # Accepts either username or email
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class ForgotPasswordIn(BaseModel):
    email_or_username: str = Field(min_length=1, max_length=120)


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)
    new_password: str = Field(min_length=1, max_length=256)


class PasscodeSetIn(BaseModel):
    passcode: str = Field(min_length=1, max_length=32)


class PasscodeVerifyIn(BaseModel):
    passcode: str = Field(min_length=1, max_length=32)


class ProfileUpdateIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=300)
    lat: float | None = None
    lng: float | None = None

