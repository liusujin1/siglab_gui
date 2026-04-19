function s = ftoa(fmtstr,x)
% ftoa.m:  floating point to ascii convertion                [pm]  2-Dec-94
%  function s = ftoa(fmtstr,x)
%  ftoa returns a string representing the number with format 
%  specified by fmtstr string argument. 
%  fmtstr: is a format description string 
%  x:      the number to be converted to ascii
%
%  If fmtstr is in the form '%nw' (where n is the field width),
%  n: the field width, not counting decimal point (since it's so skinny)
%  s: the string representation of x with the maximum resolution possible
%     while using at most w+1 characters. If the field width is too small
%     to allow even one significant digit, then '0' is returned.
% 
%  If fmtstr is in the form '%nv' (where n is the field width),
%  n: the field width, not counting decimal point (since it's so skinny)
%  s: the string representation of x with the maximum resolution possible
%     while using at exactly w+1 characters. (If a decimal point is not
%     needed, then only w characters will be used). If the field width is
%     too small to allow even one significant digit, then '0' is returned.
%
%  Otherwise, use  sprintf c conventions: sout=sprintf(fmtstr,number);
%  e.g.  ftoa('%7.2f',1000)
 
nf = length(fmtstr);  fcode = fmtstr(nf);
while fcode==' ' nf=nf-1; fcode = fmtstr(nf); end;
if fcode=='w' || fcode=='v'
   w = s2n(fmtstr(2:nf-1)); % extract field width for 'w' and 'v' formats
   y = abs(x);
   if x==0 if fcode=='v' s=[blanks(w) '0']; else s='0'; end; return;
   elseif x<0 ww = w-1;
   else ww = w;
   end;
   if     y <  1e-99  k = [6 0 1 1];
   elseif y <  1e-9   k = [5 1 1 2];
   elseif y <  .01    k = [4 0 3 3];
   elseif y <  10^ww  k = [0 0 0 0];
   elseif y <  1e10   k = [3 0 4 4];
   elseif y <  1e100  k = [4 1 2 3];
   else               k = [5 2 0 2];  end;
   if k(1) fmt = 'e'; else fmt = 'g'; end;  % select the e or g format
   j = w-k(1);  if x<0 j=j-1; end;          % compute j, the format precision
   if j<0  s = '0'; return; end;            % not enough digits available
   if j==0  k = k + [0 1 -1 -1]; end;       % e format sometimes removes the "."
   s = sprintf(['%1.' int2str(j) fmt],x);   % convert to decimal string
   n = length(s);                           % length of result
   if k(1) k = [1:w-k(2) w+k(3):w+k(4)];    % here for e format
   else    k = 1:min(w+1,n);  end;          % here for g format
   if max(k)<=n s = s(k); else s = '0'; end;  % don't go over array bounds
   if fcode=='v'
     n = w-length(s);                % number of zeroes to pad (on right)
     if isempty(findstr('.',s))      % Is it an integer?
        if n>0  s = [s '.']; end;    % Yes. Add decimal pt. before padding
     else n = n + 1;                 % No. Don't count period in field width
     end;
     %%%  s = [s char(ones(1,n)*'0')];    % Pad with zeros this does not work under all conditions RAB
     for k=1:n
         s = [s '0']; 
     end;    % Pad with zeros ... the way it used to be done

   end;
else
   s = sprintf(fmtstr,x);  
end; 
%   end function


