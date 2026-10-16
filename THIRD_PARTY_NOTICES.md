# Third-party notices

FranklinWH Local is MIT-licensed (see [LICENSE](LICENSE)). An identical copy of this file ships
inside the integration folder (`custom_components/franklinwh_local/THIRD_PARTY_NOTICES.md`),
so it travels with HACS and manual installs. It includes code adapted from the
project below, which is used under its own MIT licence.

## franklinwh-direct-connect-api — david2069

- Source: https://github.com/david2069/franklinwh-direct-connect-api
  (PyPI: https://pypi.org/project/franklinwh-direct-connect-api/,
  docs: https://david2069.github.io/franklinwh-direct-connect-api/)
- Adapted here: the smart-circuit switching write (`AgateClient.set_smart_circuit`) and the
  generator settings write (`AgateClient.set_generator`, `GENERATOR_WRITABLE`) in
  `custom_components/franklinwh_local/client.py`. Beyond the code, this integration also
  builds on the library's local command catalog, per-device battery reads, and operating-mode
  and generator write recipes.

```
MIT License

Copyright (c) 2026 david2069

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
