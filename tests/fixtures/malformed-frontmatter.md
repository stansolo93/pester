---
title: [invalid yaml
tags: {broken: [
---

This file has malformed YAML frontmatter.
The chunker should gracefully handle this by treating
the entire file as body content with empty metadata.
This is enough content to pass the minimum bytes threshold.
