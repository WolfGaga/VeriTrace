import { useRef, useMemo, useEffect, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Html, Float, MeshDistortMaterial } from '@react-three/drei';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { nodeAnimationPhase } from '@/research/graph-view';

interface NodeData {
  id: string | number;
  name: string;
  state: number;
  children?: NodeData[];
  x?: number;
  y?: number;
  z?: number;
}

interface MindMap3DProps {
  data: NodeData[];
  activeNode?: string;
  onNodeClick?: (name: string) => void;
  isEnd?: boolean;
}

interface SceneProps extends MindMap3DProps {
  depthOffset: number;
  maxDepth: number;
}

const calculatePositions = (nodes: NodeData[], depth = 0, parentPos = { x: 0, y: 0, z: 0 }): NodeData[] => {
  const result: NodeData[] = [];
  const spacing = 2.2;
  const depthSpacing = 2.8;

  nodes.forEach((node, i) => {
    const angleOffset = (i - (nodes.length - 1) / 2) * 0.7;
    const newNode = {
      ...node,
      x: parentPos.x + depthSpacing,
      y: parentPos.y + angleOffset * spacing,
      z: parentPos.z + (Math.random() - 0.5) * 0.8,
    };

    result.push(newNode);

    if (node.children?.length) {
      const childNodes = calculatePositions(
        node.children,
        depth + 1,
        { x: newNode.x!, y: newNode.y!, z: newNode.z! }
      );
      newNode.children = childNodes;
      result.push(...childNodes);
    }
  });

  return result;
};

const flattenTree = (nodes: NodeData[]) => {
  const allNodes: NodeData[] = [];
  const connections: { from: THREE.Vector3; to: THREE.Vector3; state: number }[] = [];
  const visited = new Set<string | number>();

  const traverse = (node: NodeData, parent?: NodeData) => {
    if (!node || visited.has(node.id)) return;
    visited.add(node.id);

    allNodes.push(node);

    // Only create connection if both parent and node have valid positions
    if (parent &&
        node.x !== undefined && node.y !== undefined && node.z !== undefined &&
        parent.x !== undefined && parent.y !== undefined && parent.z !== undefined) {
      connections.push({
        from: new THREE.Vector3(parent.x, parent.y, parent.z),
        to: new THREE.Vector3(node.x, node.y, node.z),
        state: node.state ?? 0,
      });
    }

    if (Array.isArray(node.children)) {
      node.children.forEach(child => {
        if (child) traverse(child, node);
      });
    }
  };

  nodes.forEach(node => {
    if (node) traverse(node);
  });

  return { allNodes, connections };
};

const GlowNode = ({
  node,
  isActive,
  onClick
}: {
  node: NodeData;
  isActive: boolean;
  onClick: () => void;
}) => {
  const meshRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Mesh>(null);
  const ringRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const [scale, setScale] = useState(0);
  const isDragging = useRef(false);
  const clickStartTime = useRef(0);

  const safeNode = {
    id: node?.id ?? 0,
    name: node?.name ?? '',
    state: node?.state ?? 0,
    x: node?.x ?? 0,
    y: node?.y ?? 0,
    z: node?.z ?? 0,
    children: node?.children,
  };
  const animationPhase = nodeAnimationPhase(safeNode.id);

  useEffect(() => {
    const timeout = setTimeout(() => setScale(1), Math.random() * 80);
    return () => clearTimeout(timeout);
  }, []);

  useFrame((state) => {
    const time = state.clock.elapsedTime;

    if (meshRef.current) {
      meshRef.current.position.y = safeNode.y + Math.sin(time * 0.8 + animationPhase) * 0.05;

      const targetScale = scale * (hovered ? 1.15 : 1);
      meshRef.current.scale.lerp(new THREE.Vector3(targetScale, targetScale, targetScale), 0.1);
    }

    if (glowRef.current) {
      const pulseScale = 1 + Math.sin(time * 2) * 0.1;
      glowRef.current.scale.setScalar(scale * pulseScale * (isActive ? 1.8 : 1.4));

      glowRef.current.rotation.z = time * 0.3;
    }

    if (ringRef.current && isActive) {
      ringRef.current.rotation.x = time * 0.5;
      ringRef.current.rotation.y = time * 0.3;
    }
  });

  const getColors = () => {
    if (isActive) return { main: '#0055ff', glow: '#00aaff', emissive: 0.8, status: 'viewing' };
    switch (safeNode.state) {
      case 1: return { main: '#f59e0b', glow: '#fbbf24', emissive: 0.7, status: 'searching' };
      case 2: return { main: '#6b7280', glow: '#9ca3af', emissive: 0.2, status: 'waiting' };
      case 3: return { main: '#059669', glow: '#10b981', emissive: 0.5, status: 'done' };
      default: return { main: '#8b5cf6', glow: '#a78bfa', emissive: 0.4, status: 'pending' };
    }
  };

  const colors = getColors();
  const isRoot = safeNode.id === 0;
  const nodeSize = isRoot ? 0.45 : 0.3;

  return (
    <Float
      speed={1.5}
      rotationIntensity={0.1}
      floatIntensity={0.3}
      floatingRange={[-0.05, 0.05]}
    >
      <group position={[safeNode.x, safeNode.y, safeNode.z]}>
        {/* Outer glow ring - disable raycasting */}
        <mesh ref={glowRef} scale={[scale * 1.4, scale * 1.4, scale * 1.4]} raycast={() => null}>
          <ringGeometry args={[nodeSize * 1.2, nodeSize * 1.6, 32]} />
          <meshBasicMaterial
            color={colors.glow}
            transparent
            opacity={isActive ? 0.4 : 0.15}
            side={THREE.DoubleSide}
          />
        </mesh>

        {/* Active ring orbit - disable raycasting */}
        {isActive && (
          <mesh ref={ringRef} scale={[scale, scale, scale]} raycast={() => null}>
            <torusGeometry args={[nodeSize * 1.8, 0.02, 16, 100]} />
            <meshBasicMaterial color="#00d4ff" transparent opacity={0.6} />
          </mesh>
        )}

        {/* Inner glow sphere - disable raycasting */}
        <mesh scale={[scale * 1.2, scale * 1.2, scale * 1.2]} raycast={() => null}>
          <sphereGeometry args={[nodeSize, 32, 32]} />
          <meshBasicMaterial
            color={colors.glow}
            transparent
            opacity={0.2}
          />
        </mesh>

        {/* Invisible larger hitbox for easier clicking */}
        <mesh
          onPointerDown={(e) => {
            e.stopPropagation();
            isDragging.current = false;
            clickStartTime.current = Date.now();
          }}
          onPointerMove={() => {
            // If pointer moves significantly, it's a drag
            if (Date.now() - clickStartTime.current > 150) {
              isDragging.current = true;
            }
          }}
          onPointerUp={(e) => {
            e.stopPropagation();
            const clickDuration = Date.now() - clickStartTime.current;
            // Quick click (< 300ms) and not dragging = valid click
            if (clickDuration < 300 && !isDragging.current) {
              const canClick = safeNode.state !== 0 && safeNode.state !== 2 && !isRoot;
              if (canClick) {
                onClick();
              }
            }
            isDragging.current = false;
          }}
          onPointerOver={(e) => {
            e.stopPropagation();
            setHovered(true);
            const canClick = safeNode.state !== 0 && safeNode.state !== 2 && !isRoot;
            if (canClick) {
              document.body.style.cursor = 'pointer';
            }
          }}
          onPointerOut={() => {
            setHovered(false);
            document.body.style.cursor = 'auto';
          }}
          visible={false}
        >
          <sphereGeometry args={[nodeSize * 2, 16, 16]} />
          <meshBasicMaterial transparent opacity={0} />
        </mesh>

        <mesh
          ref={meshRef}
          scale={[scale, scale, scale]}
          raycast={() => null}
        >
          <sphereGeometry args={[nodeSize, 64, 64]} />
          <MeshDistortMaterial
            color={colors.main}
            emissive={colors.main}
            emissiveIntensity={colors.emissive}
            roughness={0.2}
            metalness={0.8}
            distort={safeNode.state === 1 ? 0.2 : 0.05}
            speed={safeNode.state === 1 ? 3 : 1}
          />
        </mesh>

        {/* Center highlight - disable raycasting */}
        <mesh scale={[scale * 0.3, scale * 0.3, scale * 0.3]} position={[nodeSize * 0.2, nodeSize * 0.2, nodeSize * 0.5]} raycast={() => null}>
          <sphereGeometry args={[nodeSize * 0.3, 16, 16]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.6} />
        </mesh>

        <Html
          position={[0, -nodeSize - 0.4, 0]}
          center
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          <div className="flex flex-col items-center gap-0.5">
            <div
              className={`
                px-1.5 py-0.5 rounded-full text-[8px] font-bold uppercase tracking-wide flex items-center gap-1
                ${safeNode.state === 1 ? 'bg-amber-500 text-white animate-pulse' : ''}
                ${safeNode.state === 3 ? 'bg-emerald-500 text-white' : ''}
                ${isActive ? 'bg-blue-600 text-white' : ''}
                ${safeNode.state !== 1 && safeNode.state !== 3 && !isActive ? 'bg-violet-500/80 text-white' : ''}
              `}
            >
              {safeNode.state === 1 && (
                <>
                  <span className="w-1.5 h-1.5 bg-white rounded-full animate-ping" />
                  <span>Searching</span>
                </>
              )}
              {safeNode.state === 3 && !isActive && (
                <>
                  <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                    <path d="M6.5 2L3 5.5L1.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  <span>Done</span>
                </>
              )}
              {isActive && (
                <>
                  <span className="w-1.5 h-1.5 bg-white rounded-full" />
                  <span>Viewing</span>
                </>
              )}
              {safeNode.state !== 1 && safeNode.state !== 3 && !isActive && safeNode.id !== 0 && (
                <span>Pending</span>
              )}
              {safeNode.id === 0 && (
                <>
                  <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                    <circle cx="4" cy="4" r="3" stroke="currentColor" strokeWidth="1.5"/>
                  </svg>
                  <span>Start</span>
                </>
              )}
            </div>
            <div
              className={`
                px-2 py-0.5 rounded text-[9px] font-medium whitespace-nowrap
                max-w-[100px] truncate text-center
                bg-white/95 text-gray-700 shadow-sm border border-gray-200
              `}
            >
              {safeNode.name.length > 14 ? safeNode.name.slice(0, 14) + '...' : (safeNode.name || 'Node')}
            </div>
          </div>
        </Html>
      </group>
    </Float>
  );
};

const FlowingConnection = ({
  from,
  to,
  state,
  delay = 0
}: {
  from: THREE.Vector3;
  to: THREE.Vector3;
  state: number;
  delay?: number;
}) => {
  const [progress, setProgress] = useState(0);
  const particlesRef = useRef<THREE.Points>(null);

  const isValidVector = (v: THREE.Vector3) =>
    v && !isNaN(v.x) && !isNaN(v.y) && !isNaN(v.z) &&
    isFinite(v.x) && isFinite(v.y) && isFinite(v.z);

  const isValid = isValidVector(from) && isValidVector(to);

  useEffect(() => {
    if (!isValid) return;

    const timeout = setTimeout(() => {
      const interval = setInterval(() => {
        setProgress(p => {
          if (p >= 1) {
            clearInterval(interval);
            return 1;
          }
          return p + 0.12;
        });
      }, 16);
      return () => clearInterval(interval);
    }, delay);
    return () => clearTimeout(timeout);
  }, [delay, isValid]);

  useFrame((state) => {
    if (!isValid) return;
    if (particlesRef.current && progress >= 1) {
      const positions = particlesRef.current.geometry.attributes.position.array as Float32Array;
      const time = state.clock.elapsedTime;

      for (let i = 0; i < 5; i++) {
        const t = ((time * 0.5 + i * 0.2) % 1);
        positions[i * 3] = from.x + (to.x - from.x) * t;
        positions[i * 3 + 1] = from.y + (to.y - from.y) * t;
        positions[i * 3 + 2] = from.z + (to.z - from.z) * t;
      }
      particlesRef.current.geometry.attributes.position.needsUpdate = true;
    }
  });

  const currentTo = useMemo(() => {
    if (!isValid) return new THREE.Vector3();
    return new THREE.Vector3().lerpVectors(from, to, progress);
  }, [from, to, progress, isValid]);

  const color = state === 3 ? '#059669' : state === 1 ? '#f59e0b' : '#8b5cf6';

  const points = useMemo(() => {
    if (!isValid) return [];
    const mid = new THREE.Vector3().lerpVectors(from, to, 0.5);
    mid.z += 0.3;
    const curve = new THREE.QuadraticBezierCurve3(from, mid, currentTo);
    return curve.getPoints(30);
  }, [from, to, currentTo, isValid]);

  const particlePositions = useMemo(() => {
    if (!isValid) return new Float32Array(15);
    const pos = new Float32Array(15);
    for (let i = 0; i < 5; i++) {
      pos[i * 3] = from.x;
      pos[i * 3 + 1] = from.y;
      pos[i * 3 + 2] = from.z;
    }
    return pos;
  }, [from, isValid]);

  if (!isValid || points.length === 0) return null;

  return (
    <group>
      <line>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            count={points.length}
            array={new Float32Array(points.flatMap(p => [p.x, p.y, p.z]))}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial color={color} transparent opacity={0.5} linewidth={1} />
      </line>

      {progress >= 1 && (
        <points ref={particlesRef}>
          <bufferGeometry>
            <bufferAttribute
              attach="attributes-position"
              count={5}
              array={particlePositions}
              itemSize={3}
            />
          </bufferGeometry>
          <pointsMaterial
            size={0.08}
            color={color}
            transparent
            opacity={0.8}
            sizeAttenuation
          />
        </points>
      )}
    </group>
  );
};

const BackgroundGrid = () => {
  return (
    <group position={[0, 0, -3]}>
      <gridHelper
        args={[30, 30, '#e2e8f0', '#e2e8f0']}
        rotation={[Math.PI / 2, 0, 0]}
      />
    </group>
  );
};

const AmbientParticles = () => {
  const count = 40;
  const mesh = useRef<THREE.Points>(null);

  const positions = useMemo(() => {
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 20;
      pos[i * 3 + 1] = (Math.random() - 0.5) * 12;
      pos[i * 3 + 2] = (Math.random() - 0.5) * 10;
    }
    return pos;
  }, []);

  useFrame((state) => {
    if (mesh.current) {
      mesh.current.rotation.y = state.clock.elapsedTime * 0.02;
      mesh.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.01) * 0.1;
    }
  });

  return (
    <points ref={mesh}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={count} array={positions} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial
        size={0.04}
        color="#6366f1"
        transparent
        opacity={0.3}
        sizeAttenuation
      />
    </points>
  );
};

const FinalNode = ({ position, visible }: { position: THREE.Vector3; visible: boolean }) => {
  const [scale, setScale] = useState(0);
  const ringRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Mesh>(null);

  useEffect(() => {
    if (visible) {
      setTimeout(() => setScale(1), 300);
    }
  }, [visible]);

  useFrame((state) => {
    const time = state.clock.elapsedTime;
    if (ringRef.current) {
      ringRef.current.rotation.x = time * 0.5;
      ringRef.current.rotation.y = time * 0.3;
    }
    if (glowRef.current) {
      glowRef.current.scale.setScalar(scale * (1.3 + Math.sin(time * 2) * 0.1));
    }
  });

  if (!visible) return null;

  return (
    <Float speed={2} rotationIntensity={0.2} floatIntensity={0.4}>
      <group position={position}>
        <mesh ref={ringRef} scale={[scale, scale, scale]}>
          <torusGeometry args={[0.6, 0.02, 16, 100]} />
          <meshBasicMaterial color="#34d399" transparent opacity={0.5} />
        </mesh>

        <mesh ref={glowRef}>
          <sphereGeometry args={[0.4, 32, 32]} />
          <meshBasicMaterial color="#10b981" transparent opacity={0.15} />
        </mesh>

        <mesh scale={[scale, scale, scale]}>
          <sphereGeometry args={[0.35, 64, 64]} />
          <MeshDistortMaterial
            color="#10b981"
            emissive="#10b981"
            emissiveIntensity={0.4}
            roughness={0.2}
            metalness={0.8}
            distort={0.1}
            speed={2}
          />
        </mesh>

        <mesh scale={[scale * 0.4, scale * 0.4, scale * 0.4]}>
          <sphereGeometry args={[0.2, 16, 16]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.7} />
        </mesh>

        <Html position={[0, -0.6, 0]} center>
          <div className="px-2.5 py-1 bg-gradient-to-r from-accent-green to-emerald-400 text-white rounded-full text-[10px] font-semibold whitespace-nowrap shadow-lg shadow-accent-green/30 flex items-center gap-1">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M8.5 2.5L3.75 7.25L1.5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Complete
          </div>
        </Html>
      </group>
    </Float>
  );
};

const CameraController = ({ depthOffset, controlsRef }: { depthOffset: number; controlsRef: React.RefObject<OrbitControlsImpl> }) => {
  const { camera } = useThree();
  const targetX = useRef(0);

  useFrame(() => {
    targetX.current = THREE.MathUtils.lerp(targetX.current, depthOffset, 0.08);

    if (controlsRef.current) {
      controlsRef.current.target.x = targetX.current;
      controlsRef.current.update();
    }

    // Also move the camera along X to keep the same relative distance
    camera.position.x = THREE.MathUtils.lerp(camera.position.x, depthOffset, 0.08);
  });

  return null;
};

const Scene = ({ data, activeNode, onNodeClick, isEnd, depthOffset }: SceneProps) => {
  const controlsRef = useRef<OrbitControlsImpl>(null);
  const { allNodes, connections } = useMemo(() => {
    const rootNode: NodeData = {
      id: 0,
      name: 'Original Question',
      state: 3,
      x: -3.5,
      y: 0,
      z: 0,
      children: calculatePositions(data[0]?.children || [], 0, { x: -3.5, y: 0, z: 0 }),
    };
    return flattenTree([rootNode]);
  }, [data]);

  const finalPosition = useMemo(() => {
    let maxX = 0;
    let avgY = 0;
    let count = 0;
    allNodes.forEach(node => {
      if (node && (node.x || 0) > maxX) maxX = node.x || 0;
      if (node && !node.children?.length) {
        avgY += node.y || 0;
        count++;
      }
    });
    return new THREE.Vector3(maxX + 2.5, count > 0 ? avgY / count : 0, 0);
  }, [allNodes]);

  return (
    <>
      <ambientLight intensity={0.7} />
      <directionalLight position={[5, 5, 5]} intensity={0.5} />
      <pointLight position={[-5, 3, 5]} intensity={0.4} color="#6366f1" />
      <pointLight position={[5, -3, 5]} intensity={0.3} color="#0071e3" />

      <BackgroundGrid />
      <AmbientParticles />

      {connections.map((conn, i) => (
        <FlowingConnection
          key={`conn-${i}`}
          from={conn.from}
          to={conn.to}
          state={conn.state}
          delay={i * 30}
        />
      ))}

      {isEnd && allNodes
        .filter(n => n && !n.children?.length && n.x !== undefined && n.y !== undefined && n.z !== undefined)
        .map((node, i) => (
          <FlowingConnection
            key={`final-conn-${i}`}
            from={new THREE.Vector3(node.x!, node.y!, node.z!)}
            to={finalPosition}
            state={3}
            delay={connections.length * 30 + i * 50}
          />
        ))}

      {allNodes
        .filter(node => node && node.id !== undefined)
        .map((node, i) => (
          <GlowNode
            key={`node-${node.id}-${i}`}
            node={node}
            isActive={node.name === activeNode}
            onClick={() => onNodeClick?.(node.name)}
          />
        ))}

      <FinalNode position={finalPosition} visible={!!isEnd} />

      <CameraController depthOffset={depthOffset} controlsRef={controlsRef} />

      <OrbitControls
        ref={controlsRef}
        enablePan={true}
        enableZoom={true}
        enableRotate={true}
        minDistance={4}
        maxDistance={18}
        maxPolarAngle={Math.PI / 1.5}
        minPolarAngle={Math.PI / 3}
      />
    </>
  );
};

const calculateMaxDepth = (nodes: NodeData[], currentDepth = 0): number => {
  let maxDepth = currentDepth;
  nodes.forEach(node => {
    if (node.children?.length) {
      const childDepth = calculateMaxDepth(node.children, currentDepth + 1);
      maxDepth = Math.max(maxDepth, childDepth);
    }
  });
  return maxDepth;
};

const MindMap3D = ({ data, activeNode, onNodeClick, isEnd }: MindMap3DProps) => {
  const [depthOffset, setDepthOffset] = useState(0);
  const [isDraggingSlider, setIsDraggingSlider] = useState(false);

  const maxDepth = useMemo(() => {
    if (!data?.length || !data[0]?.children?.length) return 0;
    const depth = calculateMaxDepth(data[0].children);
    // Convert depth to X position (each depth level is ~2.8 units apart based on depthSpacing)
    return depth * 2.8;
  }, [data]);

  // Auto-advance slider when new nodes are added (only if not manually dragging)
  useEffect(() => {
    if (!isDraggingSlider && maxDepth > 0) {
      // Auto-follow to keep recent nodes visible, but not all the way to the end
      const autoOffset = Math.max(0, maxDepth - 5);
      setDepthOffset(prev => autoOffset > prev ? autoOffset : prev);
    }
  }, [maxDepth, isDraggingSlider]);

  if (!data?.length) {
    return (
      <div className="w-full h-[280px] flex items-center justify-center bg-gradient-to-br from-slate-50 to-indigo-50/30">
        <div className="flex flex-col items-center gap-2">
          <div className="w-12 h-12 rounded-full bg-gradient-to-br from-violet-400/20 to-blue-400/20 flex items-center justify-center">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="text-violet-500 animate-pulse">
              <circle cx="12" cy="12" r="3" fill="currentColor"/>
              <circle cx="12" cy="4" r="2" fill="currentColor" opacity="0.5"/>
              <circle cx="12" cy="20" r="2" fill="currentColor" opacity="0.5"/>
              <circle cx="4" cy="12" r="2" fill="currentColor" opacity="0.5"/>
              <circle cx="20" cy="12" r="2" fill="currentColor" opacity="0.5"/>
            </svg>
          </div>
          <div className="text-gray-500 text-sm">Waiting for thinking process...</div>
        </div>
      </div>
    );
  }

  if (!data[0]?.children?.length) {
    return (
      <div className="w-full h-[280px] flex items-center justify-center bg-gradient-to-br from-slate-50 to-indigo-50/30">
        <div className="flex flex-col items-center gap-2">
          <div className="w-12 h-12 rounded-full bg-gradient-to-br from-amber-400/20 to-orange-400/20 flex items-center justify-center">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="text-amber-500">
              <circle cx="12" cy="12" r="3" fill="currentColor"/>
              <circle cx="12" cy="4" r="2" fill="currentColor" opacity="0.5" className="animate-pulse"/>
              <circle cx="12" cy="20" r="2" fill="currentColor" opacity="0.5" className="animate-pulse" style={{animationDelay: '0.2s'}}/>
              <circle cx="4" cy="12" r="2" fill="currentColor" opacity="0.5" className="animate-pulse" style={{animationDelay: '0.4s'}}/>
              <circle cx="20" cy="12" r="2" fill="currentColor" opacity="0.5" className="animate-pulse" style={{animationDelay: '0.6s'}}/>
            </svg>
          </div>
          <div className="text-amber-600 text-sm font-medium">Initializing search tasks...</div>
          <div className="text-gray-400 text-xs">Analyzing your question</div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-[280px] overflow-hidden bg-gradient-to-br from-slate-50 via-white to-indigo-50/50 relative">
      <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-blue-400/10 to-purple-400/10 rounded-full blur-3xl" />
      <div className="absolute bottom-0 left-0 w-24 h-24 bg-gradient-to-br from-emerald-400/10 to-cyan-400/10 rounded-full blur-2xl" />

      <Canvas
        camera={{ position: [0, 0, 9], fov: 50 }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true }}
      >
        <Scene
          data={data}
          activeNode={activeNode}
          onNodeClick={onNodeClick}
          isEnd={isEnd}
          depthOffset={depthOffset}
          maxDepth={maxDepth}
        />
      </Canvas>

      {maxDepth > 0 && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-white/90 backdrop-blur-sm rounded-lg px-3 py-1.5 border border-gray-200/50 shadow-sm">
          <span className="text-[9px] text-gray-500 font-medium">Depth</span>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setDepthOffset(Math.max(0, depthOffset - 2))}
              aria-label="Previous depth"
              className="w-5 h-5 rounded bg-gray-100 hover:bg-gray-200 flex items-center justify-center text-gray-600 transition-colors"
            >
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                <path d="M6.5 2L3.5 5L6.5 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <input
              type="range"
              aria-label="Visualization depth"
              min="0"
              max={maxDepth}
              step="0.5"
              value={depthOffset}
              onChange={(e) => setDepthOffset(parseFloat(e.target.value))}
              onMouseDown={() => setIsDraggingSlider(true)}
              onMouseUp={() => setIsDraggingSlider(false)}
              onTouchStart={() => setIsDraggingSlider(true)}
              onTouchEnd={() => setIsDraggingSlider(false)}
              className="w-24 h-1 accent-violet-500 cursor-pointer"
            />
            <button
              onClick={() => setDepthOffset(Math.min(maxDepth, depthOffset + 2))}
              aria-label="Next depth"
              className="w-5 h-5 rounded bg-gray-100 hover:bg-gray-200 flex items-center justify-center text-gray-600 transition-colors"
            >
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                <path d="M3.5 2L6.5 5L3.5 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
          <button
            onClick={() => setDepthOffset(maxDepth)}
            className="px-1.5 py-0.5 rounded bg-violet-100 hover:bg-violet-200 text-[8px] text-violet-600 font-medium transition-colors"
            title="Jump to latest"
          >
            Latest
          </button>
        </div>
      )}

      <div className="absolute bottom-2 left-2 flex items-center gap-2 bg-white/80 backdrop-blur-sm rounded-lg px-2 py-1 border border-gray-200/50">
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
          <span className="text-[9px] text-gray-600">Searching</span>
        </div>
        <div className="w-px h-3 bg-gray-300" />
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          <span className="text-[9px] text-gray-600">Done</span>
        </div>
        <div className="w-px h-3 bg-gray-300" />
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-blue-600" />
          <span className="text-[9px] text-gray-600">Viewing</span>
        </div>
      </div>

      <div className="absolute bottom-2 right-2 flex items-center gap-1.5">
        <span className="px-2 py-0.5 bg-white/80 backdrop-blur-sm rounded-full text-[9px] text-gray-500 border border-gray-200/50">
          Drag to rotate · Scroll to zoom
        </span>
      </div>
    </div>
  );
};

export default MindMap3D;
