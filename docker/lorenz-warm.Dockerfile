# Optional local demo profile. First build the normal base-py3.10 image.
# Run from project root, using the small target directory as build context:
# docker build -f docker/lorenz-warm.Dockerfile -t paperharness/base-py3.10 targets/paper-lorenz
# No pysindy wheel is installed: the experiment must import checked-out source.
FROM paperharness/base-py3.10
COPY requirements.txt /tmp/lorenz-requirements.txt
ARG PIP_INDEX_URL=https://pypi.org/simple/
RUN python -m pip install --no-cache-dir -r /tmp/lorenz-requirements.txt \
    && python -m pip check
