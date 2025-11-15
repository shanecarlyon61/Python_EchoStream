# Pushing to GitHub

## Step 1: Create GitHub Repository

1. Go to https://github.com/new
2. Repository name: `Python_EchoStream` (or your preferred name)
3. Description: "Python implementation of EchoStream audio communication system"
4. Choose Public or Private
5. **DO NOT** initialize with README, .gitignore, or license (we already have these)
6. Click "Create repository"

## Step 2: Push the Code

After creating the repository, GitHub will show you commands. Use these commands in your terminal:

```bash
cd "E:\Working_Space\projects\RPI\Python_EchoStream"
git remote add origin https://github.com/YOUR_USERNAME/Python_EchoStream.git
git push -u origin main
```

**Replace `YOUR_USERNAME` with your GitHub username.**

## Alternative: Using SSH

If you have SSH keys set up:

```bash
git remote add origin git@github.com:YOUR_USERNAME/Python_EchoStream.git
git push -u origin main
```

## After Pushing

Your repository will be available at:
`https://github.com/YOUR_USERNAME/Python_EchoStream`

