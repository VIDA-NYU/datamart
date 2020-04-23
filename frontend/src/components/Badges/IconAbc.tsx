import React, { SVGAttributes } from 'react';

interface Props extends SVGAttributes<SVGElement> {
  color: string;
  size: string | number;
}

const IconAbc = (props: Props) => {
  const { color, size, ...otherProps } = props;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke={color}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...otherProps}
    >
      <path d="M1.75 17L4.75 7L8.25 17" />
      <path d="M2.75 14.5H7.25" />
      <path d="M10.75 7V17" />
      <ellipse cx="13" cy="13.5" rx="2.25" ry="3.5" />
      <path d="M21.591 15.9749C21.2763 16.4644 20.8754 16.7977 20.439 16.9327C20.0025 17.0678 19.5501 16.9985 19.139 16.7336C18.7278 16.4687 18.3764 16.0201 18.1292 15.4445C17.882 14.8689 17.75 14.1922 17.75 13.5C17.75 12.8078 17.882 12.1311 18.1292 11.5555C18.3764 10.9799 18.7278 10.5313 19.139 10.2664C19.5501 10.0015 20.0025 9.9322 20.439 10.0673C20.8754 10.2023 21.2763 10.5356 21.591 11.0251" />
    </svg>
  );
};

IconAbc.defaultProps = {
  color: 'currentColor',
  size: '24',
};

export { IconAbc };
