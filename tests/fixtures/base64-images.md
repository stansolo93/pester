---
title: Document with Images
type: reference
status: active
---

This document contains base64 images that should be stripped.

![screenshot](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==)

Some text between images. This tests the base64 stripping function.

[diagram]: <data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg==>

More content after the reference-style image. The chunker should
replace these with placeholder text and preserve the surrounding content.
