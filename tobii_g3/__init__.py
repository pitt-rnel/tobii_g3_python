"""tobii_g3: python implementation of Tobii Pro G3 API

Quickstart:
from tobii_g3 import Glasses3Client
g3 = Glasses3Client()
response = g3.battery_level()
"""

from tobii_g3.g3 import Glasses3Client

__all__ = ['Glasses3Client']