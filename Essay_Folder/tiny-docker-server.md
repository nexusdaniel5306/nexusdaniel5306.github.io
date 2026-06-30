# Scrapping together a Tiny Docker Server from old gaming PC parts

I recently finished a small home server project! I had two main motivations: To learn more about networking and containerization by deploying onto local bare metal; and to create a small machine which I could quickly deploy, test, and host personal projects on.

Like every project which seems straight forward, it started with a simple idea and immediately turned into a scavenger hunt through networking, storage, SSH, Docker, and a few mysterious boot messages.

The goal was straightforward: take a small machine, install Linux, run Docker on it, and use it as a home base for Python and JavaScript side projects. I wanted something practical, lightweight, and easy to manage from my laptop. I did not want a full-blown infrastructure science fair living under my desk.

## The Machine

I had some parts lying around from my old gaming computers, there was no better time to put them to use than now! The build was thankfully very smooth. The final  spec is as follows: a 10th-gen Intel processor, 16 GB of DDR4, and a 500 GB M.2 SSD. The only parts that came out of pocket was a mini-itx motherboard and a small case. Plenty of power for personal projects, background workers, local web apps, and experiments that needed a better home than my Macbook.

## Installing Ubuntu Server

I created an Ubuntu Server installer on a USB drive from my Mac. A smaller USB drive would have worked, but using a larger one gave me more breathing room. I used the standard AMD64 Ubuntu Server image, which is also the correct image for a modern 64-bit Intel processor.

I flashed the installer with balenaEtcher, booted from the USB's UEFI entry, and selected **Try or Install Ubuntu Server**.

During startup, the installer showed messages like:

```text
GPT PMBR size mismatch
The backup GPT table is not on the end of the device
```

That looked scary at first, but it turned out to be related to the way the bootable USB image was laid out. The installer continued normally, so I did not chase ghosts.

## Networking: Follow the Interface With an IP

I plugged in ethernet during installation, which made life much easier. Ubuntu detected multiple network interfaces, but only one received an address through DHCP:

```text
enp4s0: [xx.x.xxx IP]
```

Another interface failed automatic configuration, Wi-Fi was unnecessary, and I did not need VLANs or bonding for the initial setup.

The practical lesson was simple: during installation, look for the interface that already has a DHCP address and leave it on automatic configuration. You can make the address stable later with a DHCP reservation on the router.

## Storage: Do Not Nuke the Wrong Disk

The installer showed both the USB drive and the internal SSD.

The USB drive appeared as something like:

```text
Generic_Flash_Disk
```

The real target was:

```text
Samsung SSD 970 EVO Plus 500GB
```

Ubuntu's guided LVM layout initially allocated only 100 GB to the root filesystem and left most of the volume group unused. For a Docker server, that was not ideal. Docker images, containers, volumes, and logs usually live under the root filesystem, so giving `/` most or all of the available space made more sense.

LVM still gives me flexibility later, but for a simple home server, I wanted the storage layout to stay boring in the best possible way.

## Giving the Server an Identity

During installation, I configured credentials and enabled OpenSSH during setup so I could manage the machine from my Mac. I skipped optional bundled software because I wanted to install Docker directly from Docker's official repository afterward.

Once the server booted, I connected from my Mac with:

```bash
ssh daniel5306@[OLDserverIP]
```

And just like that, the little box was no longer a mystery machine. It was now `danserver`.

## The DHCP Plot Twist

After a reboot, SSH appeared to hang. My first instinct was to wonder whether SSH was broken, but `ping` to the old address timed out too. That was the clue.

The server had received a new DHCP address,

Checking the address locally with this command confirmed it:

```bash
ip -br addr
```

The updated SSH command became:

```bash
ssh daniel5306@[NEWserverIP]
```

This was one of the most important little lessons from the whole project: a DHCP address is not a promise. If I want `danserver` to stay easy to find, I should reserve a stable IP address in the router using the server's ethernet MAC address.

## Bootstrapping the Server

After the operating system was installed and SSH worked, I had codex make a bootstrap script which handled the basic setup steps I would rather not repeat by hand:

```text
Update Ubuntu packages
Install tools like Git, curl, jq, htop, ncdu, and UFW
Allow OpenSSH through the firewall before enabling UFW
Add Docker's official Ubuntu apt repository
Install Docker Engine, Buildx, and Docker Compose
Enable Docker at startup
Add my user to the docker group
Create /srv/apps and /srv/backups
Enable unattended security updates
```

I copied it from my Mac to the server, then I ran it:

```bash
bash /tmp/bootstrap-server.sh
```

After logging out and back in so the new Docker group membership would apply, I tested Docker with:

```bash
docker run hello-world
docker compose version
```

Those two commands are deeply satisfying when they work. They are the server equivalent of hearing an engine turn over.

## Where Apps Live

I decided that applications should live under:

```text
/srv/apps
```

A typical app directory looks like this:

```text
/srv/apps/my-app/
  Dockerfile
  compose.yaml
  .env
  src/
```

The deploy flow is intentionally simple:

```bash
cd /srv/apps/my-app
git pull
docker compose up -d --build
```

For containers that should survive reboots, I can use:

```yaml
restart: unless-stopped
```

That small line is one of Docker Compose's best quality-of-life features. It lets the server recover automatically after a reboot without turning every side project into a production operations exercise.

## GitHub Access Without Overdoing Permissions

For private repositories, I wanted the server to pull code without giving it more access than necessary.

A read-only SSH deploy key is a good fit when the server only needs access to one private repository;

The public key can be added to the GitHub repository under **Settings > Deploy keys**, with write access left unchecked.

Then the server can clone the repository:

```bash
git clone git@github.com:OWNER/REPOSITORY.git
```

The key lesson here is that cloning and pulling do not require write access. A home server should not have permission to push code unless it truly needs that ability.

## Local Services and Security

For now, the server is local-network-only. If a container needs to serve other devices on my home network, it must publish a port:

```yaml
ports:
  - "8000:8000"
```

But not every container needs a port. A background worker that only makes outbound API calls, such as a Python cron job calling an external API, usually does not need any inbound access.

For those workers, the bigger risks are leaked API keys, vulnerable dependencies, unsafe handling of untrusted data, and unexpected usage costs.

A few sensible protections are:

```yaml
services:
  worker:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    user: "1000:1000"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
```

That keeps the container more restricted and avoids publishing ports that are not needed.

## Thinking About Future Public Access

I am not exposing anything publicly yet, but I wanted to understand what the safer path might look like later.

Rather than forwarding random ports through my home router, a cleaner design would use a tunnel, access controls, and a reverse proxy:

```text
Internet
  -> HTTPS proxy or tunnel
  -> access controls
  -> home server
  -> reverse proxy
  -> selected container
```

Tools like Cloudflare Tunnel, Cloudflare Access, Tailscale, and Caddy are all useful depending on the goal.

For administration, public SSH is something I want to avoid. SSH should stay on the LAN or behind a private network like Tailscale.

## Protecting the Rest of the Home Network

Container security helps, but it does not fully isolate the rest of the home network. If I want stronger protection, the server should live on a separate VLAN, guest network, or DMZ-style network.

A simple version might look like this:

```text
Main LAN:        10.168.x.0/24
Server network: 10.168.xx.0/24
```

Router rules could then allow my Mac to SSH into the server, allow the server to reach necessary internet services, and block the server from initiating connections to laptops, phones, printers, and other home devices.

That is a future improvement, but it is worth thinking about early.

## The Current State

At the end of the project, the server is up and running as `danserver`.

It has Ubuntu Server installed, SSH works from my Mac, Docker Engine and Docker Compose are installed, and a containerized applications are serving clients across my home network.

That feels like a pretty good milestone. The machine started as a former router box asking me confusing pfSense questions, and it ended as a useful little Docker host for side projects.

## What I Learned

This project taught me a lot in a very practical way, achieving and beyond my original goals.

I learned that the right operating system depends on the role of the machine. I learned that ethernet makes server installation much easier. I learned to double-check disks before formatting anything. I learned that Docker storage planning matters because containers can quietly eat disk space. I learned that DHCP addresses can change at the least convenient time.

Most importantly, I learned that a home server can be useful without becoming complicated. Start local, keep permissions narrow, expose only what you need, and make the setup repeatable.

Going forward, I have a bucket list of things of I want to do: reserving a stable IP, adding backups, documenting each app's ports and volumes, and possibly isolating the server on its own network. Similarly as I scale applications and the machine has more processes, I want to create an diagnostic dashboard to understand what's going on in the machine better, and to trouble shoot performance issues easier.
