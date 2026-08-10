export type CssViewport = {
  width: number;
  height: number;
};

export type ResponsiveProject = {
  name: string;
  initialViewport: CssViewport;
  pairedViewport: CssViewport;
};

export const responsiveProjects: readonly ResponsiveProject[] = [
  {
    name: "iphone-17-pro",
    initialViewport: { width: 402, height: 681 },
    pairedViewport: { width: 402, height: 874 }
  },
  {
    name: "iphone-17-pro-landscape",
    initialViewport: { width: 756, height: 352 },
    pairedViewport: { width: 874, height: 402 }
  },
  {
    name: "ipad-pro-11",
    initialViewport: { width: 834, height: 1194 },
    pairedViewport: { width: 834, height: 1210 }
  },
  {
    name: "ipad-pro-11-landscape",
    initialViewport: { width: 1194, height: 834 },
    pairedViewport: { width: 1210, height: 834 }
  },
  {
    name: "small-phone-portrait",
    initialViewport: { width: 360, height: 640 },
    pairedViewport: { width: 360, height: 780 }
  },
  {
    name: "small-phone-short-landscape",
    initialViewport: { width: 667, height: 320 },
    pairedViewport: { width: 740, height: 360 }
  }
];

export function getResponsiveProject(name: string): ResponsiveProject {
  const project = responsiveProjects.find((item) => item.name === name);
  if (!project) {
    throw new Error(`Unknown responsive Playwright project: ${name}`);
  }
  return project;
}
