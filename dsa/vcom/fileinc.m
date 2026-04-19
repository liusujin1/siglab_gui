function fx=fileinc(filename,opt) 
% Increments filename if last 2 or more characters are digits.
% If fileinc is called with more than one argument (usually opt='DOF')
% then each channel label is modified as follows:
% 1.) If a channel label begins with a 1 to 3 digit number
%     (optionally with a leading + or -) followed by a direction
%     code (one of the characters x,y,z,r,p, or t) followed by a
%     single comma, then the number will be incremented. For example.
%     the channel label "78Y,crud" will be changed to "79Y,crud".
% 2.) If the direction code is followed by two consecutive commas
%     then both the direction and point numbers will be incremented.
%     For example the label "299x,," on successive saves will be
%     incremented to 299y,, 299z,, 300x,, 300y,, 300z,, etc.
% 3.) If the channel label is not one of the above forms then that
%     channel label will not be modified.
  fx = filename;              % next file for auto-incrementing file name
  k = find(fx == '.') - 1;    % last character of file name (not extension)
  j = k;                      % j points to last digit, k will point to 1st
  while fx(k)>='0' & fx(k)<='9' & k>1  k=k-1; end; % loop until not a digit
  nd = j - k;                 % number of digits found
  if nd >= 2                  % here if 2 or more digits were found
     k = k+1;                 % point to first digit
     s = sprintf(['%0' int2str(nd) 'd'],eval(fx(k:j))+1); % increment digits
     fx(k:j) = s(1:nd);       % replace digits with incremented number
     if length(s) > nd fx(k) = '0'; end;  % make sure e.g. 999 wraps to 000
     if nargin > 1
       %include
       vdlg1_h;               % indexes into handle array HVDLG1_ 
       %end_include
       global VDLG1_S2 HVDLG1_;
       for chan = 1:length(VDLG1_S2(:,1))
         c = VDLG1_S2(chan,:);              % get label for channel 1
         if c(1)=='-' |  c(1)=='+' k=2; else k=1; end;  % ignore initial +/-
         m = k;  p = c(m)-'0';              % get first digit
         if p >= 0 & p <= 9                 % was it really a digit?
           q = c(m+1)-'0';                  % yes, get 2nd digit of point
           if q >= 0 & q <= 9               % was it really a digit?
              p=10*p+q; m=m+1; q=c(m+1)-'0';  % if yes, get 3rd digit
              if q >=0 & q <=9              % was it really a digit?
                 p = 10*p+q;  m=m+1;        % here if yes
              end; 
           end;
           dc=lower(c(m+1));  s=c(m+2);     % get direction code & modifier
           if findstr('xyzrpt',dc) & s==',' % , modifier increments point
             p = int2str(p+1001);           % incr point, convert to ascii
             p = p(4-m+k:4);                % wraparound effect
             if c(m+3)==','            % another , modifier incr direction also
               m=m+1; if findstr('zt',dc) p=[p c(m)-2]; else p=c(m)+1; k=m; end;
             end;
             c(k:m)=p; VDLG1_S2(chan,:)=c;  % insert new digits and/or dir code
             c = c(1:min(findstr(c,'~'))-1);  % keep up to first tilde
             if get(HVDLG1_(chsel),'value')==chan  % are we displaying chan?
               set(HVDLG1_(chname),'string',c); % yes; update chan label
             end;
            %RAB  10/22/98
            plot_vna('set','all_labels');
            if ~isempty(findobj('tag','siglab_mc_setup'))
                 % Call mcsetup and inform it that the labels have changed.
                 % Unfortunatly, there is currently no specific call to mcsetup 
                 % for updating only the labels. Must tweak "all" objects.
                 eval( 'mcsetup(''fromvxx'',''all'')');
            end;
            %END_RAB
             
           
             disp(['Point "' c '" has been saved in file ' fx]);
           end;  % end if findstr
         end;    % end if p >= 0 & p <= 9
       end;      % end for chan = 1:length(VDLG1_S2(:,1))
     end;        % end if nargin > 1
  end;           % end if nd >= 2
% end function
