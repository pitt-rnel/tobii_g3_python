# tobii_g3

Unofficial Python implementation of the [Tobii Pro
Glasses3](https://www.tobii.com/products/eye-trackers/wearables/tobii-pro-glasses-3)
API, using websockets

The official Tobii Pro Glasses3 Developer Guide can be found
[here](https://go.tobii.com/tobii-pro-glasses-3-developer-guide).

# Installation
The library can be installed with pip after cloning the code from github From
the repository root directory, run 
```bash
pip install .
```

# Quick Start
This example will attempt to automatically discover and connect to a Glasses3
unit on the network and request the battery level
```python
from tobii_g3 import G3Client
g3 = G3Client()
response = g3.battery_level()
```

# Alternatives
This library predates the release of the official Glasses3 python SDK
[here](https://github.com/tobiipro/g3pylib).