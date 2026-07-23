# GPU stressor + CUDA health probe for the thermal campaign.
#
# Built as an image rather than compiled on the host on purpose: the build
# context is streamed to the Docker daemon, so this works even when the caller
# runs in a different mount namespace than dockerd (bind mounts silently
# resolve to empty directories in that case).
#
#   docker build -t nivuus/gpuburn:1 scripts/thermal-campaign/
#   docker run --rm --gpus all nivuus/gpuburn:1 60          # 60 s burn
#   docker run --rm --gpus all --entrypoint /usr/local/bin/cudadiag nivuus/gpuburn:1
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

COPY gpuburn.cu cudadiag.cu /src/
RUN nvcc -O2 -o /usr/local/bin/gpuburn  /src/gpuburn.cu -lcublas && \
    nvcc -O2 -o /usr/local/bin/cudadiag /src/cudadiag.cu

ENTRYPOINT ["/usr/local/bin/gpuburn"]
