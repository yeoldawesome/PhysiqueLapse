# GPU setup (NVIDIA driver + Container Toolkit)

This lets the PhysiqueLapse container use the RTX 3060 Ti. It changes the host (kernel module, Docker runtime) and **needs a reboot**, so you run it at a quiet time. Better Lift doesn't use the GPU. It only goes down during the reboot, and its systemd services start again by themselves.

As checked on 2026-11-23: there's no driver, `nvidia-smi` doesn't exist, and `ubuntu-drivers` recommends **`nvidia-driver-595-open`**.

## 0. Before you start

```bash
systemctl is-active betterlift-pb betterlift-push cloudflared   # active x3
mokutil --sb-state                                              # Secure Boot on or off?
```

If Secure Boot is **enabled**, the install may ask you to set a one-time password. At the next boot a blue "MOK management" screen appears. Choose *Enroll MOK* and enter that password. You need a keyboard and screen for this. Ubuntu's prebuilt, signed driver modules usually avoid the prompt.

## 1. Driver

```bash
sudo apt-get update
sudo ubuntu-drivers list --gpgpu        # what it would install
sudo ubuntu-drivers install             # installs the recommended driver (595-open)
sudo reboot
```

After the reboot:

```bash
nvidia-smi                              # shows the RTX 3060 Ti, driver 595.x, CUDA 13.x
systemctl is-active betterlift-pb betterlift-push cloudflared
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/health
```

## 2. NVIDIA Container Toolkit

From NVIDIA's docs ([install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)):

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker           # restarts containers too; PhysiqueLapse comes back by itself
```

## 3. Check a container can see the GPU

```bash
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

It should print the same table as on the host.

## 4. Check everything else is fine

```bash
systemctl is-active betterlift-pb betterlift-push cloudflared
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/health
docker compose -f /opt/physiquelapse/docker-compose.yml ps     # physiquelapse (healthy)
```

Then open Better Lift's public URL once. Tell me when step 3 works, and I'll benchmark and switch PhysiqueLapse to its GPU image (`docker-compose.gpu.yml`, coming in the GPU milestone).

## Undo

```bash
sudo apt-get remove --purge 'nvidia-driver-*' nvidia-container-toolkit && sudo apt-get autoremove && sudo reboot
```
