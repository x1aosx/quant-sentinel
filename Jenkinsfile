pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    parameters {
        string(
            name: 'HOST_PORT',
            defaultValue: '8082',
            description: '宿主机对外端口（容器内固定 80）'
        )
    }

    environment {
        PROJECT_NAME = 'quant-sentinel'
        FRONTEND_DIR = 'web'
        API_DIR = 'api'
        BASE_PATH = '/quant-sentinel/'
        REGISTRY = 'registry.shawsx.com:8443'
        RUNTIME_IMAGE = "${REGISTRY}/library/python-web-stack:latest"
        IMAGE_REPOSITORY = "${REGISTRY}/library/${PROJECT_NAME}"
        NODE_HOME = '/var/jenkins_home/tools/node-v24'
        HOST_PORT = "${params.HOST_PORT}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Frontend') {
            steps {
                dir(env.FRONTEND_DIR) {
                    withEnv(["PATH+NODE=${NODE_HOME}/bin"]) {
                        sh 'npm config set registry https://registry.npmmirror.com'
                        sh 'npm ci'
                        sh './node_modules/.bin/tsc -b'
                        sh './node_modules/.bin/vite build --base="$BASE_PATH"'
                    }
                }
            }
        }

        stage('Build Docker Image') {
            steps {
                sh 'docker pull "$RUNTIME_IMAGE"'

                script {
                    env.IMAGE_TAG = sh(
                        returnStdout: true,
                        script: 'date +%Y%m%d%H%M'
                    ).trim()
                }

                sh '''\
                    docker build \\
                        --build-arg BASE_PATH="$BASE_PATH" \\
                        -t "$IMAGE_REPOSITORY:$IMAGE_TAG" \\
                        -t "$IMAGE_REPOSITORY:latest" \\
                        .
                '''
            }
        }

        stage('Push Image') {
            steps {
                sh 'docker push "$IMAGE_REPOSITORY:$IMAGE_TAG"'
                sh 'docker push "$IMAGE_REPOSITORY:latest"'
            }
        }

        stage('Deploy') {
            steps {
                sh 'IMAGE_TAG="$IMAGE_TAG" HOST_PORT="$HOST_PORT" docker compose -f deploy/docker-compose.yaml up -d'
                sh 'docker image prune -f'
            }
        }
    }

    post {
        success {
            echo "部署成功：${PROJECT_NAME} 已使用镜像 ${IMAGE_REPOSITORY}:${IMAGE_TAG} 更新。"
        }
        failure {
            echo '部署失败：请检查 Jenkins 控制台输出、Docker 构建日志和目标主机容器状态。'
        }
    }
}
