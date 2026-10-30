import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface GradientSphereProps {
  position: [number, number, number];
  color: string;
  scale?: number;
  speed?: number;
}

function GradientSphere({ position, color, scale = 1, speed = 0.5 }: GradientSphereProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const initialPosition = useMemo(() => position, []);

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.position.y = initialPosition[1] + Math.sin(state.clock.elapsedTime * speed) * 0.5;
      meshRef.current.position.x = initialPosition[0] + Math.sin(state.clock.elapsedTime * speed * 0.7) * 0.3;
    }
  });

  return (
    <mesh ref={meshRef} position={position} scale={scale}>
      <sphereGeometry args={[1, 32, 32]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={0.15}
      />
    </mesh>
  );
}

function GradientPlane() {
  const meshRef = useRef<THREE.Mesh>(null);

  const gradientMaterial = useMemo(() => {
    return new THREE.ShaderMaterial({
      uniforms: {
        uTime: { value: 0 },
        uColor1: { value: new THREE.Color('#0071e3') },
        uColor2: { value: new THREE.Color('#bf5af2') },
        uColor3: { value: new THREE.Color('#ff375f') },
      },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform float uTime;
        uniform vec3 uColor1;
        uniform vec3 uColor2;
        uniform vec3 uColor3;
        varying vec2 vUv;

        void main() {
          float t = uTime * 0.1;
          vec2 uv = vUv;

          // Create flowing gradient
          float noise = sin(uv.x * 3.0 + t) * cos(uv.y * 2.0 - t * 0.5) * 0.5 + 0.5;

          vec3 color = mix(uColor1, uColor2, noise);
          color = mix(color, uColor3, sin(uv.x * 2.0 - t) * 0.3 + 0.3);

          gl_FragColor = vec4(color, 0.05);
        }
      `,
      transparent: true,
      side: THREE.DoubleSide,
    });
  }, []);

  useFrame((state) => {
    if (gradientMaterial.uniforms) {
      gradientMaterial.uniforms.uTime.value = state.clock.elapsedTime;
    }
  });

  return (
    <mesh ref={meshRef} position={[0, 0, -15]} rotation={[0, 0, 0]}>
      <planeGeometry args={[40, 30]} />
      <primitive object={gradientMaterial} attach="material" />
    </mesh>
  );
}

function Scene() {
  return (
    <>
      <ambientLight intensity={0.5} />

      <GradientPlane />

      <GradientSphere
        position={[-6, 2, -8]}
        color="#0071e3"
        scale={3}
        speed={0.4}
      />
      <GradientSphere
        position={[7, -1, -10]}
        color="#bf5af2"
        scale={2.5}
        speed={0.6}
      />
      <GradientSphere
        position={[-3, -3, -12]}
        color="#ff375f"
        scale={2}
        speed={0.5}
      />
      <GradientSphere
        position={[5, 4, -14]}
        color="#64d2ff"
        scale={1.5}
        speed={0.3}
      />
    </>
  );
}

export function GradientMesh() {
  return (
    <div className="fixed inset-0 -z-10 pointer-events-none">
      <Canvas
        camera={{ position: [0, 0, 5], fov: 60 }}
        gl={{ antialias: true, alpha: true }}
        style={{ background: 'transparent' }}
      >
        <Scene />
      </Canvas>
    </div>
  );
}

// Fallback while the 3D background loads.
export function GradientMeshFallback() {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <div
        className="absolute inset-0"
        style={{
          background: `
            radial-gradient(ellipse at 20% 30%, rgba(0, 113, 227, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 20%, rgba(191, 90, 242, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse at 60% 80%, rgba(255, 55, 95, 0.04) 0%, transparent 50%),
            radial-gradient(ellipse at 10% 70%, rgba(100, 210, 255, 0.05) 0%, transparent 50%)
          `,
        }}
      />

      <div
        className="absolute w-[600px] h-[600px] rounded-full opacity-20 animate-float-slow"
        style={{
          background: 'radial-gradient(circle, rgba(0, 113, 227, 0.3) 0%, transparent 70%)',
          left: '10%',
          top: '20%',
          filter: 'blur(60px)',
        }}
      />
      <div
        className="absolute w-[500px] h-[500px] rounded-full opacity-15 animate-float"
        style={{
          background: 'radial-gradient(circle, rgba(191, 90, 242, 0.3) 0%, transparent 70%)',
          right: '15%',
          top: '10%',
          filter: 'blur(50px)',
          animationDelay: '2s',
        }}
      />
      <div
        className="absolute w-[400px] h-[400px] rounded-full opacity-10 animate-float-slower"
        style={{
          background: 'radial-gradient(circle, rgba(255, 55, 95, 0.3) 0%, transparent 70%)',
          left: '30%',
          bottom: '10%',
          filter: 'blur(40px)',
          animationDelay: '4s',
        }}
      />
    </div>
  );
}

export default GradientMesh;
