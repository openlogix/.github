# The Open Logix pre-release policy, the one place it's set: publish-pre-release
# deletes older pre-releases by it and states it in every release text.
# Pre-releases are built daily, so keeping the newest PRERELEASE_KEEP means
# each one is deleted after about PRERELEASE_KEEP days, unless made stable.
PRERELEASE_KEEP=5
