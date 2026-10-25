"""Descriptor-based trusted ancestry checks for private application directories."""

import os
import stat
from pathlib import Path


def trusted_directory(path: Path, *, create: bool = False) -> None:
    """Reject links/untrusted owners and writable non-sticky ancestors.

    Root and the service UID are trusted. Sticky ancestors may be shared, but
    their children must still have trusted owners. The leaf is always private.
    No-follow relative opens bind each check to the directory actually opened.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Directory must be absolute without .. components")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, name in enumerate(path.parts[1:]):
            parent = os.fstat(fd)
            if parent.st_uid not in {0, os.getuid()} or (
                parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX
            ):
                raise ValueError("Directory ancestry must have trusted ownership and permissions")
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            leaf = index == len(path.parts) - 2
            if info.st_uid not in ({os.getuid()} if leaf else {0, os.getuid()}):
                raise ValueError("Directory must be owned by a trusted user")
            if leaf and info.st_mode & 0o022:
                raise ValueError("Directory must be owned privately, not writable by group/others")
    except OSError:
        raise ValueError("Directory is missing, inaccessible, or uses symbolic links") from None
    finally:
        os.close(fd)
