# The Open Logix pre-release policy, the one place it's set: publish-pre-release
# deletes older pre-releases by it and states it in every release text.
# Pre-releases are built daily: the newest PRERELEASE_KEEP are kept, and
# older ones too while a remote config on main names them (any Open Logix
# repo, version or channel); the rest are deleted. Stable releases never are.
PRERELEASE_KEEP=5
