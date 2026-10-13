from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# Upper bound stops someone sending a 1 MB "password" to make argon2 burn CPU.
Password = Field(min_length=8, max_length=128)


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Password


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class MeOut(BaseModel):
    id: UUID
    email: str
    role: Literal["customer", "admin"]
