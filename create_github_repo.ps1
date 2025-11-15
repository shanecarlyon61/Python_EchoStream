# PowerShell script to create GitHub repository and push code
# Usage: .\create_github_repo.ps1 -GitHubUsername "your_username" -RepoName "Python_EchoStream"

param(
    [Parameter(Mandatory=$true)]
    [string]$GitHubUsername,
    
    [Parameter(Mandatory=$false)]
    [string]$RepoName = "Python_EchoStream",
    
    [Parameter(Mandatory=$false)]
    [string]$Description = "Python implementation of EchoStream audio communication system",
    
    [Parameter(Mandatory=$false)]
    [string]$GitHubToken = $env:GITHUB_TOKEN
)

Write-Host "Creating GitHub repository: $RepoName" -ForegroundColor Green

# Check if GitHub token is available
if (-not $GitHubToken) {
    Write-Host "Warning: GITHUB_TOKEN environment variable not set." -ForegroundColor Yellow
    Write-Host "Creating repository via API requires authentication." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please create the repository manually:" -ForegroundColor Cyan
    Write-Host "1. Go to https://github.com/new" -ForegroundColor Cyan
    Write-Host "2. Repository name: $RepoName" -ForegroundColor Cyan
    Write-Host "3. Description: $Description" -ForegroundColor Cyan
    Write-Host "4. Choose Public or Private" -ForegroundColor Cyan
    Write-Host "5. DO NOT initialize with README, .gitignore, or license" -ForegroundColor Cyan
    Write-Host "6. Click 'Create repository'" -ForegroundColor Cyan
    Write-Host ""
    
    # Add remote and show push commands
    $repoUrl = "https://github.com/$GitHubUsername/$RepoName.git"
    Write-Host "After creating the repository, run these commands:" -ForegroundColor Cyan
    Write-Host "git remote add origin $repoUrl" -ForegroundColor White
    Write-Host "git push -u origin main" -ForegroundColor White
    exit
}

# Create repository via GitHub API
$headers = @{
    "Authorization" = "token $GitHubToken"
    "Accept" = "application/vnd.github.v3+json"
}

$body = @{
    name = $RepoName
    description = $Description
    private = $false
    auto_init = $false
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body -ContentType "application/json"
    
    Write-Host "Repository created successfully!" -ForegroundColor Green
    Write-Host "Repository URL: $($response.html_url)" -ForegroundColor Green
    
    # Add remote
    $repoUrl = "https://github.com/$GitHubUsername/$RepoName.git"
    Write-Host "Adding remote origin..." -ForegroundColor Cyan
    git remote add origin $repoUrl
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Remote already exists, updating..." -ForegroundColor Yellow
        git remote set-url origin $repoUrl
    }
    
    # Push code
    Write-Host "Pushing code to GitHub..." -ForegroundColor Cyan
    git push -u origin main
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Code pushed successfully!" -ForegroundColor Green
        Write-Host "Repository available at: $($response.html_url)" -ForegroundColor Green
    } else {
        Write-Host "Push failed. Please check your credentials and try again." -ForegroundColor Red
    }
} catch {
    Write-Host "Error creating repository: $_" -ForegroundColor Red
    Write-Host "Please create the repository manually on GitHub." -ForegroundColor Yellow
}

