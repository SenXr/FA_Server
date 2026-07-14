from __future__ import annotations


class FTPHandler:
    """Placeholder boundary for deployment-specific FTP transfer code."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def download(self, *args, **kwargs):
        raise NotImplementedError("FTP transfer is deployment-specific.")
